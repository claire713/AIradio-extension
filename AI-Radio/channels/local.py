
"""
File : local.py
Author: Claire Murphy
Date: May 2026

Local personalised AI channel. Channel is activated by pressing "L" key or button number 5

When activated : 

- Channel scans for known BLE devices (BBCm microbit)
- Upon connection, the listener profile is retrieved from local json file and broadcast is started
- LLM generates content locally using Ollama API (llama 3.2:3B model)
- Generated Text is synthesised to speech using Local Piper TTS engine
- Locally stored music clips are played between generated segments 
- Microbit sends movement status in real time over BLE UART characteristic to determine music selection and voice style
- asyncio tasks used for excecution of couroutines (https://docs.python.org/3/library/asyncio-task.html#id5)

Inline references are provided throughout the file at the point of use.
AI tools were used for debugging assistance during the development of this file. 
All AI-generated suggestions were reviewed and approved by the author before implementation.
The code provides comments where AI suggestions were adopted.
"""

import json
import asyncio
import subprocess
import re
import os
import wave
from ollama import generate
from ollama import AsyncClient
from piper import PiperVoice 
from bleak import BleakClient,BleakScanner
from base_channel import BaseChannel
import logging


UART_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  
AUDIO_CHUNK = 4096      # (0.18s at 22050Hz sample rate for wav files)
SONG_CMD = object()     # sentinel object used to signal TTS loop to play song
STOP = object()         # sentinl object used to signal TTS to exit loop


class Local(BaseChannel):
	
	def __init__(self, config):       	
		"""
		Initialise local channel - Called by supervisor during startup.
		"""
		super().__init__(config)  		# call parent class (Base channel) constructor 
		
        #--find absolute path to audio folder relative to script location--
		script_dir = os.path.dirname(os.path.abspath(__file__))
		self.audio_dir = os.path.join(script_dir,config.get("audio_dir","audio"))
		
		#--load voices for local TTS  (uncomment alternative voices to change. Additional voices requires downloading files from github.com/OHF-Voice/piper1-gpl)--
		self.voice1 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-semaine-medium.onnx"))   
		self.voice2 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-northern_english_male-medium.onnx")) 
		#Alternative voices
		#self.voice1 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-alba-medium.onnx"))  
		#self.voice2 = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-jenny_dioco-medium.onnx"))
		
		#--Get MAC addresses of compatible devices from congig YAML file--
		mac_addresses = config.get("device_address",[])
		if isinstance(mac_addresses, str):       #Accept single string and tuple of mac addresses from config file
			mac_addresses = [mac_addresses]
		self.device_address: tuple[str,...] = tuple(mac_addresses)   #AI assistance used for syntax of isinstance check and tuple type hint
		
		#--Get LLM and audio file names from config YAML file--
		self.llm_model = config.get("llm_model")
		self.relax = config.get("relax_songs")
		self.upbeat = config.get("upbeat_songs")

		#--Asyncio tasks, events and client for coordinating channel state and LLM streaming--
		self.stop_ch = asyncio.Event()
		self.connect = None
		self.tts_q = None
		self.ble_disconnect = None
		self.segment_done = None
		self.schedule_audio_task = None
		self.client = AsyncClient()
		self.audio_process = None
		
		#--Initial playback and display state--
		self.status = "still"
		self.voice = self.voice1     
		self.encval = 100     #magic eye value - default used to match magic eye command in existing system. Won't affect anything in icon display
		self.relax_song = 0
		self.upbeat_song = 0
		
		#--Warmup LLM when channel is loaded to reduce latency during first call--	
		print("loading llm model...")
		try:
			generate(model=self.llm_model,prompt="hi",keep_alive=-1)    #keep_alive=-1 keeps model loaded in RAM indefinitely 
		except Exception:
			print("LLM warmup failed")



#------------------------------------Channel control functions-------------------------------------------
	async def play(self):
		"""
		Called by supervisor when "L" key or button 5 on the radio is pressed.
		Send magic eye signal to display local icon and and creates the BLE scanning task.
		play() is called repeatedly by the supervisor while channel is active.
		"""
		if self.connect is None or self.connect.done():  #Prevent duplicate BLE connect tasks
			self.magic_eye.send("local",0x04BF)          #send "local" command to display house icon on OLED display 
			self.stop_ch.clear()
			print("Local Channel activated")
			#reset events set from previous channel activations
			if self.ble_disconnect is not None:
				self.ble_disconnect.clear()
			if self.segment_done is not None:
				self.segment_done.clear()
			print("starting BLE scanning task")
			self.connect = asyncio.create_task(self.connectDevice())       #create connectDevice task
	
	
	async def stop(self):
		"""
		Called by supervisor when channel is deactivated.
		Stop Channel cleanly by signaling exits,stopping audio,cancelling tasks,draining qeueus
		"""
		print(f"[{self.name}] Stopping")
		self.stop_ch.set()  #Set stop channel event
		self.stop_audio()   #Close ffplay subprocesses
		
		#Set events to unblock coroutines waiting on them
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.segment_done is not None:
			self.segment_done.set()

		#Drain queue and send stop schedule_audio() loop
		if self.tts_q is not None:
			self.drain_tts_q()
			await self.tts_q.put(STOP)

		#Cancel running tasks (#https://docs.python.org/3/library/asyncio-task.html#asyncio.Task.cancel)			
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
			
		self.magic_eye.send("clear",self.encval)  #clear channel icon display

		
#------------------------------------Audio processing functions-------------------------------------------

	def initialise_audio(self,sample_rate=22050, channels=1):
		"""
		Starts an ffplay subprocess to play raw PCM audio streamed to its stdin pipe. (https://ffmpeg.org/ffplay.html)
        Only starts a new process if one is not already running.
        Default sample rate matches Piper TTS output of 22050Hz.
		"""
		  
		if self.audio_process and self.audio_process.poll() is None:   #check if process (ffplay) object has been created and is still running
			return
		try:
			self.audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)  #launch ffplay subprocess to receive raw PCM audio via stdin pipe
		except FileNotFoundError:       #AI assistance used for Error handling approach
			print("ffplay not found")
		except Exception:
			print("failed to start ffplay")

			
	def write_audio(self,audio_bytes):
		"""
		Writes raw PCM bytes to ffplay process stdin
		Flushes immediately after each write to prevent buffering causing audio gaps.
        Called by synthesise() and play_song() to stream audio to ffplay
		"""
		if self.audio_process and self.audio_process.stdin:    #check ffplay process is running and has stdin pipe open
			try:
				#print("writing audio data")
				self.audio_process.stdin.write(audio_bytes)   #write raw PCM bytes to ffplay stdin pipe
				self.audio_process.stdin.flush()              #forces pipe to ssend data immediately to prevent buffering delays
			except (BrokenPipeError, ValueError):             #AI assistance used for specific exceptions to catch
				print("audio write skipped : ffplay pipe closed")

					
	def stop_audio(self):
		"""
		Stops audio and shutdown process cleanly
		"""
		if self.audio_process is None:
			return
		print("stopping audio process")

		try:
			if self.audio_process.stdin:           #AI assistance used for shutdown pattern of audio process
				self.audio_process.stdin.close()   #close stdin first, ffplay exits automatically due to -autoexit flag
		except Exception:
			print("ffplay closing error")
		try:
			self.audio_process.wait(timeout=0.5)  #wait 0.5 seconds for exit,if not kill process
			self.audio_process = None
		except subprocess.TimeoutExpired:   #force kill process if process is not exited in time
			print("killing process")
			try:
				self.audio_process.kill()
			except Exception:
				print("could not kill audio process")
			finally:	
				self.audio_process = None 

#------------------------------------Speech synthesis and content scheduling functions-------------------------------------------
	async def schedule_audio(self):
		"""
		Audio scheduling task - consumes items from TTS queue and processes them.
        Text strings are synthesised to speech using Piper TTS and streamed to ffplay.
        SONG_CMD sentinel triggers music playback and sets segment_done event.
        STOP sentinel exits the loop cleanly.
        Created as an asyncio task in play_radio() before content generation begins.
		"""
		print("TTS called")
		while True:
			text = await self.tts_q.get()    #waits until text is in the tts queue 
			#print(text)
			try:
				if text is SONG_CMD:        #check queue for sentinel object for song command to schedule music
					await self.play_song()
					self.segment_done.set()
					print("Song complete, segment_done set")
					continue
				if text is STOP:           #check queue for sentinel object for stop command to Stop exit and stop TTS loop
					print("TTS stop sentinel received")
					return
				await self.synthesise(text)  #call TTS synthesis function
			except asyncio.CancelledError:
				raise   #raise cancellation to exit the loop cleanly
			except Exception as e:
				print("audio schedule error",e)

	async def synthesise(self,text):
		"""
		Synthesises text to speech using Piper TTS and streams raw PCM audio chunks to ffplay.
        Piper synthesize() is synchronous so runs in a separate thread (asyncio.to_thread())
        to avoid blocking the asyncio event loop during synthesis.
		"""
		def run_tts():
			for chunk in self.voice.synthesize(text):   #stream in chunks. piper API synthesize function : https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md
				if self.stop_ch.is_set():        #exit mid sentence if channel is stopped
					break
				if self.audio_process is None:
					self.initialise_audio(chunk.sample_rate,chunk.sample_channels or 1)
				self.write_audio(chunk.audio_int16_bytes)   #write chunk to audio
		#Run TTS in seperate thread to avoid blocking	
		await asyncio.to_thread(run_tts)   # https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread

	async def play_song(self):
		"""
		Selects and plays a music clip based on current listener movement status.
		Plays a pre-recorded transition message before the music clip
		Music selection is rule-based - upbeat playlist when active, relaxed playlist when still.
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
			self.relax_song = (self.relax_song + 1) % len(self.relax)  #AI assistance used for index syntax to loop through playlist without going out of bounds
		song_path = os.path.join(self.audio_dir,song)
		message_path = os.path.join(self.audio_dir,message)
		#play prerecorded transition 
		with wave.open(message_path, "rb") as w:
			while True:
				data = w.readframes(AUDIO_CHUNK)
				if not data:
					break
				self.write_audio(data)
				await asyncio.sleep(0)
		#play music clip
		with wave.open(song_path, "rb") as w:
			while True:
				data = w.readframes(AUDIO_CHUNK)
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
				self.tts_q.get_nowait()   #removes item from queue until empty
		except asyncio.QueueEmpty:  #raises QueueEmpty when queue is empty   (https://docs.python.org/3/library/asyncio-queue.html#asyncio.Queue.get_nowait)
				pass
		
#------------------------------------LLM functions-------------------------------------------
	async def generate_content(self,prompt):
		"""
		Stream LLM output from ollama API and push sentences to TTS qeueu
		"""
			
		buffer = ""  	# buffer used to store partial text during streaming
		print("generating content...")
		try:
			async for chunk in await self.client.generate(model='llama3.2:3b',prompt=prompt,keep_alive=-1,stream=True) :   #ollama async streaming generate function. (Ollama python function for API : https://github.com/ollama/ollama-python)
				if self.ble_disconnect.is_set() or self.stop_ch.is_set():
					print("OLLAMA Generation interupted")
					break	
				text = chunk.get("response","")                 #returns token text from chunk
				buffer = buffer + text                             #add text to buffer
				sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
				for s in sentences:
					await self.tts_q.put(s.strip())   #add sentence to queue
					print("Sentence Queued for TTS")			
		except Exception as e:
			print("error generating content",e)
	
	#handle leftover text in buffer after loop		
		if buffer.strip():
			await self.tts_q.put(buffer.strip())
		print("generation complete")
		
		
	def split_sentence(self,text):
		"""
		#function to split buffer into sentences and return remaining parts of sentences
		"""
		#AI assistance used for regex and remainder syntax.
		parts = re.split(r'([.?!])',text)   #text split at punctuation that occurs at the end of sentences [text,punctuation,text,punctuation,text] (https://docs.python.org/3/library/re.html#re.split)
		sentences = []
		for i in range(0, len(parts)-1, 2):          #increment by 2 as every second item in parts is the text
			sentences.append(parts[i]+parts[i+1])    #include sentence and punctuation
	    #handling leftover text
		rem = parts[-1] if len(parts) % 2 else ""  
		return sentences,rem			
			
#------------------------------------Prompt generation functions-------------------------------------------
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

#------------------------------------Radio Orchestration function-------------------------------------------
	async def play_radio(self,name,interests,reminder):
		"""
		Scheduling of radio broadcast for BLE connected listener
		Creates audio scheduling task, plays pre-recorded intro clips, then enters the broadcast loop alternating AI generated segments with music clips.
        Broadcast schedule: pre-recorded intro - AI intro segment - music - AI information segment (per interest) - music - AI general knowledge segment - music (loops)
		"""
		self.schedule_audio_task = asyncio.create_task(self.schedule_audio(), name="tts_worker")  #Create audio scheduling task before content generation so it is ready to consume queue items

        #play radio welcome clips - Sample rate and channels read from WAV file header to configure ffplay correctly (https://docs.python.org/3/library/wave.html)
		print("playing pre-recorded welcome clips")
		intro_files = [
		os.path.join(self.audio_dir,"intro_22050.wav"),		
		os.path.join(self.audio_dir,"Welcome_intro.wav")
		]
		try:
			for file in intro_files:
				with wave.open(file, "rb") as w:
					fs = w.getframerate()
					channels = w.getnchannels()
					if self.audio_process is None:
						self.initialise_audio(sample_rate=fs,channels=channels)  #initialise audio process 
					while True:
						data = w.readframes(AUDIO_CHUNK)   #read data frames
						if not data:
							break
						self.write_audio(data)		  #write data to audio process
		except Exception:
			print("completed writing welcome clips to")
		finally:
			print("intro complete")
		radio_index = 0
		intro = False
		try:
			while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
				if radio_index<len(interests):
					if intro == False:
						#Generate personlaised intro segment first
						await self.generate_content(self.intro_prompt(name,interests,reminder))
						await self.tts_q.put(SONG_CMD)  #send song command to queue to play song
						await self.segment_done.wait()  #wait for music to finish and then clear for next event to prevent overlapping music and content
						self.segment_done.clear()
						intro = True
					else:
						#Generate personlised intro segment for current interest
						await self.generate_content(self.gen_prompt(name,interests[radio_index],reminder))
						await self.tts_q.put(SONG_CMD)
						await self.segment_done.wait()
						self.segment_done.clear()
						radio_index = radio_index+1
				else:
					#loop with general knowledge segment when all interests are covered
					await self.generate_content(self.gen_prompt(name, "general knowledge", reminder))
					await self.tts_q.put(SONG_CMD)
					await self.segment_done.wait()
					self.segment_done.clear()

		#AI assistance used to suggest exception handling and finally cleanup structure			
		except asyncio.CancelledError:
			raise
		except Exception:
			print("Error in broadcast loop")
		finally:
			if self.tts_q is not None:
				await self.tts_q.put(STOP) #send stop signal to qeueu
			if self.schedule_audio_task is not None and not self.schedule_audio_task.done():
				await self.schedule_audio_task  #wait for task to cancel 



#------------------------------------BLE functions-------------------------------------------		
	
	async def scan(self,address):
		"""
		Scans for known BLE devices and returns the device with the strongest RSSI signal.
        Runs in a loop until a known device is found or the channel is stopped.
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

			#Scan for 5 seconds to allow time to detect nearby devices
			scanner = BleakScanner(callback)    #scan for using bleak API : https://bleak.readthedocs.io/en/latest/api/index.html#
			try:
				print("BLE scan cycle started")
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
				s = max(bbc,key = lambda b:bbc[b][1])     #connect to microbit that has the strongest signal strength (AI assistance suggested lambda syntax - when provided with logic from author)
				dev = bbc[s][0]
				print("BLE device found : ",dev.name)
				return dev	
		return None		
	
	async def start_broadcast(self,client,ID):
		"""
		Retrieves listener profile from local JSON file using device MAC address as identifier and starts the radio broadcast.
		Falls back to default profile if device ID not found.
        Listener data remains on device at all times.
		"""
		db_file = os.path.join(self.audio_dir,"users.json")
		
		with open(db_file,'r') as file:	
			data = json.load(file)  #parse JSON file into Python dictionary
		
		if ID in data["users"]:
			#match mac address of device to listeners profile
			print("User present")
			user = data["users"][ID]
			name = user["name"]
			interests = [user["interest1"],user["interest2"]]
			reminders = user["reminder1"],user["reminder2"]		
		else:
			#default listener profile
			print("Device ID not found in JSON file")
			
			name = "Listener" 
			interests = ["General knowledge","Space"]
			reminders = ["drink water","water the plants"]	
		   
		  #call play_radio with retrieved listerner information to start broadcast 
		await self.play_radio(name, interests, reminders)

			
	async def notify_register(self,client):
		"""
		subscribe to BLE notification characteristic so movement status is received in real time
		"""
		#Callback called automatically by Bleak each time a UART notification is received
		def uart_callback(sender,data):
			self.status = data.decode("utf-8").strip()  #syntax suggested by AI when debugging
			print("movement status received: {self.status}")
			#change voice of speaker based on movement status
			if self.status == "still":
				self.voice = self.voice1
			elif self.status == "active":
				self.voice = self.voice2
			print("status updated...",self.status)
		#Subscribe to UART notification characteristic
		await client.start_notify(UART_UUID,uart_callback)   #(https://bleak.readthedocs.io/en/latest/api/client.html)
			
	async def connectDevice(self):
		"""
		Main BLE connection loop: scans for known device, connects, retrieves listener profile and starts radio broadcast.
        Runs until channel is stopped. Returns to scanning on disconnect.
		"""
		while not self.stop_ch.is_set():
			device = await self.scan(self.device_address)
			if device is None:
				break     #exit in the case of channel stopping during scanning 
				
			# Fresh state for each new connection
			self.tts_q = asyncio.Queue()          # (https://docs.python.org/3/library/asyncio-queue.html)
			self.ble_disconnect = asyncio.Event()
			self.segment_done = asyncio.Event()
			self.audio_process = None
			task = None
			
			#bluetooth disconnect handler
			def dis_callback(_):
				print("disconnect handler called")
				self.ble_disconnect.set()       #signal exit for broadcast loop
				self.drain_tts_q()             #drain TTS qeueu
				self.tts_q.put_nowait(STOP)   #sent stop signal to TTS qeueu
				self.stop_audio()            #stop ffplay process
				if task and not task.done():  
					task.cancel()           #cancel broadcast task if still running
			try:
				print("Connecting to BLE device...")
				async with BleakClient(device.address, disconnected_callback=dis_callback) as client:   
					print("Connected to Micro:Bit\n")
					self.ble_disconnect.clear()           #clear ble disconnect event for new connection
					ID = str(device.address)             #retireve microbit mac address for profile identification
					await self.notify_register(client)  #call function to subscribe to BLE notification characteristic
					task = asyncio.create_task(self.start_broadcast(client,ID))  #create async broadcast task
					try:
						await task
					except asyncio.CancelledError:
						if self.stop_ch.is_set():   # channel stopped - exit connectDevice()
							return 
			except Exception as e:
				print("BLE connection Error",e)
			await asyncio.sleep(0.7)

#------------------------------------Enocder input functions-------------------------------------------

	async def on_encoder_A_input(self, value: int):
		"""Required by BaseChannel interface - encoder input not used in this channel."""
		print("encoder A")

	async def on_encoder_B_input(self, value: int):
		"""Required by BaseChannel interface - encoder input not used in this channel."""
		print("encoder B")
		
		
