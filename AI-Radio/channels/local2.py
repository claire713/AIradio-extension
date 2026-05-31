
"""
File : local.py

Local personalised AI channel. Channel is activated by pressing "C" key.
When activated : 

- Channel scans for known BLE devices (BBCm microbit)
- Upon connection, user profile is retrieved from local json file and broadcast is started
- LLM generates content locally using Ollama API (llama 3.2:3B model)
- Generated Text is synthesised to speech using Local Piper TTS engine
- Locally stored music clips are played between generated segments 
- Microbit sends movement status in real time over BLE UART characteristic to determine music selection and voice style
- asyncio tasks used for concurrent excecution of couroutines (https://docs.python.org/3/library/asyncio-task.html#id5)


"""

import json
import asyncio
import time
import threading
import queue
import subprocess
import re
import os
import wave
from ollama import generate
from ollama import AsyncClient
from piper import PiperVoice 
from bleak import BleakClient,BleakScanner
from queue import Empty
from base_channel import BaseChannel
import logging


_UART_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  
_AUDIO_CHUNK = 4096     # (0.18s at 22050Hz sample rate for wav files)
SONG_CMD = object()     #sentinel object used to signal TTS loop to play song
STOP = object()         #sentinl object used to signal TTS to exit loop


class Local(BaseChannel):
	
	def __init__(self, config):       	
		"""
		Initialise local channel - Called by supervisor during startup.
		"""
		
		super().__init__(config)  		#call parent class (Base channel) constructor 
		
        #--find absolute path to audio folder relative to script location--
		script_dir = os.path.dirname(os.path.abspath(__file__))
		self.audio_dir = os.path.join(script_dir,config.get("audio_dir","audio"))
		
		#--load voices for local TTS  (uncomment to change. Additional voices requires downloading files from github.com/OHF-Voice/piper1-gpl)--
		self.voice1 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-semaine-medium.onnx"))  
		self.voice2 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-northern_english_male-medium.onnx")) 
		#self.voice1 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-alba-medium.onnx"))  
		#self.voice2 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-jenny_dioco-medium.onnx"))
		self.voice = self.voice1     #initial voice
		
		#--BLE configuration--
		mac_addresses = config.get("device_address",[])
		if isinstance(mac_addresses, str):       #accept single string and tuple of mac addresses from config file
			mac_addresses = [mac_addresses]
		self.device_address: tuple[str,...] = tuple(mac_addresses)
		
		#--LLM config--
		self.llm_model = "llama3.2:3b"
		#config.get("llm_model")
		#Get audio file names
		self.relax = config.get("relax_songs")
		self.upbeat = config.get("upbeat_songs")

		
		self.stop_ch = asyncio.Event()
		self.connect = None
		self.tts_q = None
		self.intro_done = None
		self.ble_disconnect = None
		self.segment_done = None
		self.schedule_audio_task = None
		self.intro_task = None
		self.client = AsyncClient()

		#--Initialise concurrency and state objects--
		self.audio_process = None
		#self.audio_lock = threading.Lock()
		self.segment_done = None
		self.disconnect_time = 0

		#--Mode state--
		self.status = "still"
		self.encval = 100     #magic eye value - default used to match magic eye command in existin system. Won't affect anything in icon display
		self.segment = True
		self.relax_song = 0
		self.upbeat_song = 0
		
		#--Warmup LLM to reduce latency during first call--	
		print("loading llm model...")
		try:
			generate(model=self.llm_model,prompt="warmup",keep_alive=-1)  
		except Exception:
			print("LLM warmup failed")

		
	async def play(self):
		"""
		Called by supervisor when "C" key is pressed.
		Send magic eye signal to display local icon and start BLE task
		"""
		self.magic_eye.send("local",0x04BF)        #send "local" command to display house icon on OLED display
		
	#Make sure Task is started once channel is activated as play function is called repeatedly while channel is running 
		if self.connect is None or self.connect.done():   
	#		self.magic_eye.send("local",0x04BF)
			self.stop_ch.clear()
			#reset events set from previous channel activations
			if self.ble_disconnect is not None:
				self.ble_disconnect.clear()
			if self.intro_done is not None:
				self.intro_done.clear()
			if self.segment_done is not None:
				self.segment_done.clear()
			print("starting BLE scanning task")
			logging.info(f"[{self.name}] | Starting BLE scanning task")
			self.connect = asyncio.create_task(self.connectDevice())       
	
	
	async def stop(self):
		"""
		Stop Channel cleanly by signaling exits,stopping audio,cancelling tasks,draining qeueus
		"""
		print(f"[{self.name}] Stopping")
		self.stop_ch.set()
		#Close ffplay subprocesses
		self.stop_audio()
		#unblock coroutines waiting on ble disconnect and finishing intro
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.intro_done is not None:
			self.intro_done.set()
		if self.segment_done is not None:
			self.segment_done.set()

	#Drain queue and send Stop sentinel to unblock tts()
		if self.tts_q is not None:
			self.drain_tts_q()
			await self.tts_q.put(STOP)

	#Cancel running tasks 	
		if self.intro_task is not None and not self.intro_task.done():
			self.intro_task.cancel()
			try:
				await self.intro_task
			except asyncio.CancelledError:
				pass
			
		if self.schedule_audio_task is not None and not self.schedule_audio_task.done():
			self.schedule_audio_task.cancel()
			try:
				await self.schedule_audio_task 
			except asyncio.CancelledError:
				pass
			
		if self.connect is not None and not self.connect.done():
			self.connect.cancel()
			try:
				await self.connect
			except asyncio.CancelledError:
				pass
			logging.info(f"[{self.name}] | BLE Task cancelled")
					
		self.magic_eye.send("clear",self.encval)

		
#------------------Audio processing functions-------------------------

	def initialise_audio(self,sample_rate=22050, channels=1):
		"""
		starts an ffplay process to consume raw PCM audio on stdin
		"""
		
		#with self.audio_lock:     
		if self.audio_process and self.audio_process.poll() is None:			#check is process (ffplay) object has been created and is still running
			return
		
		try:
			self.audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)
		
		except FileNotFoundError:
			print("ffplay not found")
		except Exception:
			print("failed to start ffplay")

			
	def write_audio(self,audio_bytes):
		"""
		Writes raw PCM bytes to ffplay process stdin
		"""
		if self.audio_process and self.audio_process.stdin:    #check ffplay process is running and has stdin pipe open
			try:
				#print("writing audio data")
				self.audio_process.stdin.write(audio_bytes)
				self.audio_process.stdin.flush()              #forces pipe to ssend data immediately to prevent buffering delays
			except (BrokenPipeError, ValueError):
				print("audio write skipped : ffplay pipe closed")

					
	def stop_audio(self):
		"""
		Stops audio and shutdown process cleanly
		"""
		#with self.audio_lock:
		if self.audio_process is None:
			return
		print("stopping audio process")
		try:
			if self.audio_process.stdin:
				self.audio_process.stdin.close()   
		except Exception:
			print("ffplay closing error")
		try:
			self.audio_process.wait(timeout=0.5)  #wait 0.5 seconds for exit,if not kill process
			self.audio_process = None
		except subprocess.TimeoutExpired:
			print("killing process")
			try:
				self.audio_process.kill()
			except Exception:
				print("could not kill audio process")
			finally:	
				self.audio_process = None #set process to None to allow new process to be created on next audio play attempt

#------------------TTS functions-------------------------
	async def schedule_audio(self):
		"""
		Consume text items from tts qeueu. Text is synthesised by piper and streamed directly to ffplay as raw PCM chunks
		"""
		print("TTS called")
		await self.intro_done.wait() #wait for welcome playback to be done
		while True:
			text = await self.tts_q.get()    #waits until text is in the tts queue 
			#print(text)
			try:
				if text is SONG_CMD:        #check queue for sentinel object for song command to schedule music
					await self.play_song()
					self.segment_done.set()
					continue
				if text is STOP:           #check queue for sentinel object for song command to Stop exit and stop TTS loop
					return
				await self.synthesise(text)  #call TTS synthesis function
			except asyncio.CancelledError:
				raise
			except Exception as e:
				print("audio schedule error",e)

	async def synthesise(self,text):
		def run_tts():
			for chunk in self.voice.synthesize(text):   #stream in chunks. piper API synthesize function : https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md
				if self.stop_ch.is_set():        #exit mid sentence if channel is stopped
					break
				if self.audio_process is None:
					self.initialise_audio(chunk.sample_rate,chunk.sample_channels or 1)
				self.write_audio(chunk.audio_int16_bytes)   #write chunk to audio
		#Run TTS in seperate thread to avoid blocking	
		await asyncio.to_thread(run_tts) 


	async def play_song(self):
		"""
		chooses song based on current movement status. 
		"""
		
		print("play song function called")
		if self.status == "active":
			#choose music from upbeat playlist when person is moving
			song = self.upbeat[self.upbeat_song]
			message = "song_message_upbeat.wav"  
			self.upbeat_song = (self.upbeat_song + 1) % len(self.upbeat)
		else:
			#choose music from relaxed playlist when person is still
			song = self.relax[self.relax_song]
			message = "song_message_relax.wav" 
			self.relax_song = (self.relax_song + 1) % len(self.relax)
		
		song_path = os.path.join(self.audio_dir,song)
		message_path = os.path.join(self.audio_dir,message)
		with wave.open(message_path, "rb") as w:
			while True:
				data = w.readframes(_AUDIO_CHUNK)
				if not data:
					break
				self.write_audio(data)
				await asyncio.sleep(0)
		with wave.open(song_path, "rb") as w:
			while True:
				data = w.readframes(_AUDIO_CHUNK)
				if not data:
					break
				self.write_audio(data)
				await asyncio.sleep(0)
	
				
	def drain_tts_q(self):
		"""
	    remove queue items
		"""
		try:
			while True:
				self.tts_q.get_nowait()
		except asyncio.QueueEmpty:
				pass

#------------------LLM functions----------------------

	async def generate_content(self,prompt):
		"""
		Stream LLM output from ollama API and push sentences to TTS qeueu
		"""
			
		buffer = ""  	# buffer used to store partial text during streaming
		print("generating content...")
		try:
			async for chunk in await self.client.generate(model='llama3.2:3b',prompt=prompt,keep_alive=-1,stream=True) :   #ollama generate function from python API : https://github.com/ollama/ollama-python
				if self.ble_disconnect.is_set() or self.stop_ch.is_set():
					print("Generation interupted")
					logging.info(f"[{self.name}] | Ollama LLM generation stopped")
					break
		
				# Ollama puts generation stats in the last chunk
				if chunk.get("done", False):
					final_chunk = chunk
				text = chunk.get("response","")                 #returns token text from chunk
				
				buffer = buffer + text                             #add text to buffer
				sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
				for s in sentences:
					print("sentence ready")
					#await self.intro_done.wait()
					logging.info(f"[{self.name}] | Sentence Queued for TTS")
					await self.tts_q.put(s.strip())   #add sentence to queue
					
		except Exception as e:
			print("error generating content",e)
	
	#handle leftover text in buffer after loop		
		if buffer.strip():
			#await self.intro_done.wait()
			await self.tts_q.put(buffer.strip())
		print("generation complete")
		
		
	def split_sentence(self,text):
		"""
		#function to split buffer into sentences and return remaining parts of sentences
		"""
		parts = re.split(r'([.?!])',text)   #text split at punctuation that occurs at the end of sentences [text,punctuation,text,punctuation...]
		sentences = []
	
		for i in range(0, len(parts)-1, 2):           #increment by 2 as every second item in parts is the text
			sentences.append(parts[i]+parts[i+1])    #include sentence and punctuation
		
	    #handling leftover text
		rem = parts[-1] if len(parts) % 2 else ""  
		return sentences,rem			
			

	
	async def intro(self):
		"""
		#function for introduction when bluetooth is intiailly connected to system ie. person's presence is detected
		"""
		self.intro_done.clear()
		#self.magic_eye.send("local",0xF800)
		print("calling intro")
		
		intro_files = [
		os.path.join(self.audio_dir,"intro_22050.wav"),		
		os.path.join(self.audio_dir,"Welcome_intro.wav")
		]
		logging.info(f"[{self.name}] | Intro audio started")
		try:
			for file in intro_files:
				with wave.open(file, "rb") as w:
					fs = w.getframerate()
					channels = w.getnchannels()
					if self.audio_process is None:
						self.initialise_audio(sample_rate=fs,channels=channels)
			
					while True:
						data = w.readframes(_AUDIO_CHUNK)
						if not data:
							break
							logging.info(f"[{self.name}] writing intro chunk")
						self.write_audio(data)
						await asyncio.sleep(0)
		except Exception:
			print("error playing wav file")
		finally:
			print("intro complete")
			logging.info(f"[{self.name}] | Intro audio bytes written to ffplay")
			self.intro_done.set()    #set intro complete event
		print("intro finsihed calling")
	
	
#------------------prompt functions----------------------	
	
	def gen_prompt(self,name,interest,reminder):
		"""
		Inject segment prompt with user information for segments about user interests
		"""
		seg_prompt = f"""You are a knowledgeable radio presenter about {interest}, speaking on the "Local Artificial Intelligence Radio Channel" to a single listener.
Deliver a spoken,personalised radio-style factual segment for an ongoing broadcast.
Greet {name} by name, reflect on their movement status : {self.status} while listening to radio, and subtly include their reminders: {reminder}.
weave in interesting facts related to {interest}. Facts should feel natural and conversational, not like a list.
Do not include sound effects or notes. Keep it warm and engaging. Only use ASCII Characters. Only use commas, letters and fullstops.
Length: approximately 100 words
"""
		return seg_prompt


	def intro_prompt(self,name,interests,reminder):
		"""
		Inject segment prompt with user information for first generated segment providing an overview of all of the users interests
		"""
		seg_prompt = f"""You are a radio presenter speaking on the "Local Artificial Intelligence Radio Channel" to a single listener.
Deliver a spoken,personalised radio-style introduction segment for an ongoing broadcast.
Greet {name} by name, reflect on their movement status : {self.status} while listening to radio, and subtly include their reminders: {reminder}.
Acknowledge whats coming up on the show : {interests}. Information should feel natural and conversational, not like a list.
Do not include sound effects or notes. Keep it warm and engaging. Only use ASCII Characters. Only use commas, letters and fullstops.
Length: approximately 70 words
"""
		return seg_prompt


#------------------Radio Orchestration functions----------------------

	async def play_radio(self,name,interests,reminder):
		"""
		Scheduling of radio broadcast for BLE connected listener
		"""
		print("playing radio")
		
		#create intro and audio tasks
		self.schedule_audio_task = asyncio.create_task(self.schedule_audio(), name="tts_worker")
		self.intro_task = asyncio.create_task(self.intro(), name="intro")

		radio_index = 0
		intro = False
		#BROADCAST SCHEDULE = intro segment - music - information1 segment - music - information 2 segment - music - general knowledge - music (loop back to general knowledge segment continously)
		try:
			while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
				if radio_index<len(interests):
					if intro == False:
						await self.generate_content(self.intro_prompt(name,interests,reminder))
						await self.tts_q.put(SONG_CMD)
						await self.segment_done.wait()
						self.segment_done.clear()
						intro = True
					else:
						await self.generate_content(self.gen_prompt(name,interests[radio_index],reminder))
						await self.tts_q.put(SONG_CMD)
						await self.segment_done.wait()
						self.segment_done.clear()
						radio_index = radio_index+1
				else:
					await self.generate_content(self.gen_prompt(name, "general knowledge", reminder))
					await self.tts_q.put(SONG_CMD)
					await self.segment_done.wait()
					self.segment_done.clear()
					
		except asyncio.CancelledError:
			raise
		except Exception:
			logging.exception(f"[{self.name}] | Error in broadcast loop")
		finally:
			if self.tts_q is not None:
				await self.tts_q.put(STOP) #send stop signal to qeueu
			await asyncio.gather(self.schedule_audio_task, self.intro_task,return_exceptions=True) #wait for tasks to cancel tasks to finish



		
#------------------BLE functions----------------------
	
	async def scan(self,address):
		"""
		scans for BLE devices until known mac address is found
		"""
		while not self.stop_ch.is_set():
			bbc = {}  #stores found compatible ble devices
			print("BLE scanning...")
			
			#callback function for found devices
			def callback(d, adv_data):
				for mac in address:
					if d.address == mac:
						bbc[d.address] = (d,adv_data.rssi)   #get rssi of compatible devices 
						print(bbc[d.address])
			scanner = BleakScanner(callback)    #scan for using bleak API : https://bleak.readthedocs.io/en/latest/api/index.html#
			try:
				logging.info(f"[{self.name}] | BLE scan cycle started")
				await scanner.start()
				await asyncio.sleep(5)
			except asyncio.CancelledError:
				print("BLE scan cancelled")
				raise
			except Exception:
				print("BLE scanner error")
				continue
			finally:
				try:
					await scanner.stop()
				except Exception:
						print("stopping scanner error")
			
			if bbc:
				s = max(bbc,key = lambda b:bbc[b][1])     #connect to microbit that has the strongest signal strength
				dev = bbc[s][0]
				print("BLE device found : ",dev.name)
				return dev	
		return None		
	
						
	async def start_broadcast(self,client,ID):
		"""
		Look up listener profile from local json file and start the radio broadcast.
		"""
		
		db_file = os.path.join(self.audio_dir,"users.json")
		
		with open(db_file,'r') as file:	
			data = json.load(file)
		
		if ID in data["users"]:
			print("User present")
			user = data["users"][ID]
			name = user["name"]
			interests = [user["interest1"],user["interest2"]]
			reminders = user["reminder1"],user["reminder2"]		
		else:
			#default listener profile
			print("Device ID not found in JSON file")
			
			name = "Listener" 
			interests = ["General knowledge","Space",]
			reminders = ["drink water","water the plants"]	
		   
		  #call play_radio to start broadcast 
		await self.play_radio(name, interests, reminders)

			
	async def notify_register(self,client):
		"""
		subscribe to BLE notification characteristic so movement status is received in real time
		"""
		
		#uart service callback function on bbc microbit - change voice of speaker based on movement status
		def uart_callback(sender,data):
			self.status = data.decode("utf-8").strip()
			logging.info(f"[{self.name}] | movement status received: {self.status}")
			if self.status == "still":
				self.voice = self.voice1
			elif self.status == "active":
				self.voice = self.voice2
			print("status updated...",self.status)
	
		#enable indications for uart service
		await client.start_notify(_UART_UUID,uart_callback)
			
	
		
	async def connectDevice(self):
		"""
		Scan for known device,connect,retrieve profile and stream radio broadcast
		"""
		while not self.stop_ch.is_set():
			device = await self.scan(self.device_address)
			if device is None:
				break
				
			# Fresh state for each connection
			self.tts_q = asyncio.Queue()       #https://docs.python.org/3/library/asyncio-queue.html
			self.intro_done = asyncio.Event()
			self.ble_disconnect = asyncio.Event()
			self.segment_done = asyncio.Event()
			self.audio_process = None
			task = None
			
			#bluetooth disconnect handler
			def on_dis(_):
				print("disconnect handler called")
				self.ble_disconnect.set()
				self.drain_tts_q()
				try:
					self.tts_q.put_nowait(STOP)
				except Exception:
					pass
				self.stop_audio()
				if task and not task.done():
					task.cancel()
			try:
				print("Connecting to BLE device...")
				async with BleakClient(device.address, disconnected_callback=on_dis) as client:   
					print("connected to device\n")
					logging.info(f"[{self.name}] | Connected to BLE device")
					self.ble_disconnect.clear()
					ID = str(device.address)            #retireve microbit mac address for profile identification
					await self.notify_register(client)  #subscribe to BLE notification characteristic
					task = asyncio.create_task(self.start_broadcast(client,ID))  #create async broadcast task
					try:
						await task
					except asyncio.CancelledError:
						pass
			except Exception as e:
				print("BLE connection Error",e)
			await asyncio.sleep(0.7)

	

	async def on_encoder_A_input(self, value: int):
		print("encoder A")

	async def on_encoder_B_input(self, value: int):
		print("encoder B")
		
		
