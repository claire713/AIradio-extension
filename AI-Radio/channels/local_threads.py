
"""
Add comments about overall system
  - name: Local
    class: channels.local.Local
    magic_colour: 0xFFF
    auto_start: false
    button: L
    requires_internet: false
    audio_dir: audio/ai_seg
    llm_model: "llama3.2:3b"
    llm_keep_alive: "1h"
    device_address:
      - "F7:7D:A1:5F:95:D7"
      - "FD:37:9B:E6:BA:0A"
    ble_scan_interval: 5.0
    ble_reconnect_delay: 0.7

"""

import json
import asyncio
import time
import subprocess
import threading
import queue
import subprocess
import re
import os
import wave
from ollama import generate
from piper import PiperVoice 
from datetime import datetime
from bleak import BleakClient,BleakScanner
from queue import Empty
from base_channel import BaseChannel
import logging

_UART_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
_AUDIO_CHUNK = 4096

class Local(BaseChannel):
	
	def __init__(self, config):       	
		"""
		Initialise local channel
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
		self.llm_model = config.get("llm_model")
		self.relax = config.get("relax_songs")
		self.upbeat = config.get("upbeat_songs")
		
		#--Initialise concurrency and state objects--
		self.connect = None
		self.tts_q = None 									
		self.intro_done = None	  						
		self.ble_disconnect = None
		self.stop_ch = threading.Event()
		self.audio_process = None
		self.audio_lock = threading.Lock()
		self.tts_thread = None
		self.intro_thread = None
		self.segment_done = None
		self.disconnect_time = 0

		#--Mode state--
		self.status = "still"
		self.duration = "15 seconds"
		self.encval = 100
		self.segment = True
		self.relax_song = 0
		self.upbeat_song = 0
		
		#--Warmup LLM to reduce latency during first call--	
		#print("loading llm model...")
	#	try:
	#		generate(model=self.llm_model,prompt="warmup",keep_alive=-1,options={"num_predict":1,"num_thread":4,})  
	#	except Exception:
	#		print("LLM warmup failed")

		
	async def play(self):
		"""
		Send magic eye signal to display local icon and start BLE task
		"""
		self.magic_eye.send("local",self.encval)
		if self.connect is None or self.connect.done():
			self.stop_ch.clear()
			logging.info(f"[{self.name}] | Channel activated")
			logging.info(f"[{self.name}] | House icon set ")
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
		Stop Channel cleanly by signaling thread exits,stopping audio,cancelling tasks,draining qeueus
		"""
		print(f"[{self.name}] Stopping")
		logging.info(f"[{self.name}] | Stop signal received")
		self.stop_ch.set()
		logging.info(f"[{self.name}] | Closing Audio process")
		#Close ffplay subprocesses
		self.stop_audio()
		
		#unblock threads waiting on ble disconnect and finishing intro
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.intro_done is not None:
			self.intro_done.set()
		if self.segment_done is not None:
			self.segment_done.set()
		#Drain TTS qeueu and unblock TTS thread by sending a stop signal
		if self.tts_q is not None:
			self.drain_tts_q()
			logging.info(f"[{self.name}] | TTS Queue drained")
			#self.tts_q.put(None)
		    #self.clear_tts_q(self.tts_q)
			try:
				self.tts_q.put_nowait(None)  #send stop signal to qeueu
				logging.info(f"[{self.name}] | Stop signal sent to Queue")
			except Exception:
				print("Stop signal could not send to qeueu")
		
		
		#cleanup threads in background
		if self.tts_thread is not None and self.tts_thread.is_alive():
			self.tts_thread.join(timeout=5.0)
			logging.info(f"[{self.name}] | TTS thread exited")
		if self.intro_thread is not None and self.intro_thread.is_alive():
			self.intro_thread.join(timeout=5.0)
			logging.info(f"[{self.name}] | Intro thread exited")
			print("waiting for intro thread to exit")
		
		#kill asyncio BLE task
		if self.connect is not None and not self.connect.done():
			self.connect.cancel()
			try:
				await self.connect
			except asyncio.CancelledError:
				pass
			logging.info(f"[{self.name}] | BLE Task cancelled")
					
		self.magic_eye.send("clear",self.encval)
		logging.info(f"[{self.name}] | icon cleared ")
	
	
	
	
#------------------Audio processing functions-------------------------

	def initialise_audio(self,sample_rate=22050, channels=1):
		"""
		starts an ffplay process to consume raw PCM audio on stdin
		"""
		
		with self.audio_lock:     
			if self.audio_process and self.audio_process.poll() is None:			#check is process (ffplay) object has been created and is still running
				return
		
		try:
			self.audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)
			logging.info(f"[{self.name}] | ffplay audio process started")
		
		except FileNotFoundError:
			print("ffplay not found. ffmpeg needs to be installed on PATH")
		except Exception:
			print("failed to start ffplay")

			
	def write_audio(self,audio_bytes):
		"""
		Writes raw PCM bytes to ffplay process stdin
		"""
		if self.audio_process and self.audio_process.stdin:
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
		with self.audio_lock:
			if self.audio_process is None:
				return
			print("stopping audio process")
			try:
				if self.audio_process.stdin:
					self.audio_process.stdin.close()   
			except Exception:
				print("ffplay closing error",exc_info=True)
				
			try:
				self.audio_process.wait(timeout=0.5)  #wait 0.5 seconds for exit,if not kill process
				self.audio_process = None
			except subprocess.TimeoutExpired:
				print("killing process")
				try:
					self.audio_process.kill()
				except Exception:
					print("could not kill audio process")
				except Exception:
					print("error waiting for ffplay")
				finally:	
					self.audio_process = None
			logging.info(f"[{self.name}] | ffplay audio process stopped")
#------------------TTS functions-------------------------
	def tts(self):
		"""
		Consume text items from tts qeueu. Text is synthesised by piper and streamed directly to ffplay as raw PCM chunks
		"""
		print("TTS called")
		
		while True:
			text = self.tts_q.get()    #waits until text is in the tts queue 
			#print(text)
			try:
				if text == "_song_.":
					logging.info(f"[{self.name}] | Song command received from queue")
					if self.status == "active":
						print("play upbeat song")
						self.song = self.upbeat[self.upbeat_song]
						self.upbeat_song = (self.upbeat_song + 1) % len(self.upbeat)
					else:
						self.song = self.relax[self.relax_song]
						self.relax_song = (self.relax_song + 1) % len(self.relax)
				
					print("play song!")
					with wave.open(os.path.join(self.audio_dir,self.song), "rb") as w:
						while True:
							data = w.readframes(_AUDIO_CHUNK)
							if not data:
								break
							self.write_audio(data)
					self.segment_done.set()
					logging.info(f"[{self.name}] | Song bytes written to ffplay")
					continue
							
				if text is None:
					logging.info(f"[{self.name}] | TTS worker stopped")
					return

				for chunk in self.voice.synthesize(text):	#generate audio in chunks using piper
					if self.stop_ch.is_set():
						break
					if self.audio_process is None:
						self.initialise_audio(sample_rate=chunk.sample_rate,channels=chunk.sample_channels or 1)	
				#print(chunk)
					self.write_audio(chunk.audio_int16_bytes)  #call audio process write function with chunk (raw PCM bytes as input)
			except Exception as e:
				print("TTS worker error - continued",e)
			finally:
				self.tts_q.task_done()                         #mark task as complete
			
			
	def drain_tts_q(self):
		try:
			while True:
				self.tts_q.get_nowait()
				self.tts_q.task_done()
		except Empty:
				pass

#------------------LLM functions----------------------


		
	def generate_content(self,prompt):
		"""
		Stream LLM output from ollama API and push sentences to TTS qeueu
		"""
			
		buffer = ""  						   # buffer used to store partial text during streaming
		print("generating content...")
		try:
			logging.info(f"[{self.name}] | Calling ollama LLM")
			for chunk in  generate(model='llama3.2:3b',prompt=prompt,keep_alive=-1,stream=True) :
				if self.ble_disconnect.is_set():
					print("Generation interupted by BLE disconnect")
					logging.info(f"[{self.name}] | Ollama LLM generation stopped")
					break
		
				text = chunk.get("response","")             #returns token text from chunk
				#text = text.encode("ascii", "ignore").decode()
				
				buffer = buffer + text                     #add text to buffer
				sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
				for s in sentences:
					print("sentence ready - waiting for intro complete")
					self.intro_done.wait()
					logging.info(f"[{self.name}] | Sentence Queued for TTS")
					self.tts_q.put(s.strip())
					
		except Exception as e:
			print("error generating content",e)
	
	#handle leftover text in buffer after loop		
		if buffer.strip():
			self.intro_done.wait()
			self.tts_q.put(buffer.strip())
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
			

	
	def intro(self):
		"""
		#function for introduction when bluetooth is intiailly connected to system ie. person's presence is detected, runs thread,sets intro_done when complete
		"""
		
		self.intro_done.clear() 
	
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
		except Exception:
			print("error playing wav file")
		print("intro complete")
		logging.info(f"[{self.name}] | Intro audio bytes written to ffplay")
		self.intro_done.set()    #set intro complete event
	
	
	
#------------------prompt functions----------------------	
	
	
	def gen_prompt(self,name,interest,reminder,duration):
		"""
		Inject segment prompt with user information for segments about user interests
		"""
		
		seg_prompt = f"""You are a knowledgeable radio presenter about {interest}, speaking on the "Cloud Artificial Intelligence Radio Channel" to a single listener.
Deliver a spoken,personalised radio-style factual segment for an ongoing broadcast.
Greet {name} by name, reflect their current movement status {self.status}, and subtly include their reminders: {reminder}.
weave in interesting facts related to {interest}. Facts should feel natural and conversational, not like a list.
Do not include sound effects or notes. Keep it warm and engaging. Only use ASCII Characters. Only use commas, letters and fullstops.
Length: approximately 100 words
"""
		return seg_prompt


	def intro_prompt(self,name,interests,reminder,duration):
		"""
		Inject segment prompt with user information for first generated segment providing an overview of all of the users interests
		"""
		seg_prompt = f"""You are a radio presenter speaking on the "Cloud Artificial Intelligence Radio Channel" to a single listener.
Deliver a spoken,personalised radio-style introduction segment for an ongoing broadcast.
Greet {name} by name, reflect their current movement status {self.status}, and subtly include their reminders: {reminder}.
Acknowledge whats coming up on the show : {interests}. Information should feel natural and conversational, not like a list.
Do not include sound effects or notes. Keep it warm and engaging. Only use ASCII Characters. Only use commas, letters and fullstops.
Length: approximately 70 words
"""
		return seg_prompt



#------------------Radio Orchestration functions----------------------

	def play_radio(self,name,interests,reminder,duration):
		"""
		Scheduling of radio broadcast for BLE connected listener
		"""
		print("playing radio")
		self.tts_thread = threading.Thread(target=self.tts,daemon=False,name=f"tts_worker")
		self.intro_thread = threading.Thread(target=self.intro, daemon=False,name=f"intro")
		self.intro_thread.start()
		self.tts_thread.start()
	
		radio_index = 0
		intro = False
		try:
			while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
				if radio_index<len(interests):
					if intro == False:
						self.generate_content(self.intro_prompt(name,interests,reminder,duration))
						self.tts_q.put("_song_.")
						self.segment_done.wait()
						self.segment_done.clear()
						intro = True
					else:
						self.generate_content(self.gen_prompt(name,interests[radio_index],reminder,duration))
						self.tts_q.put("_song_.")
						self.segment_done.wait()
						self.segment_done.clear()
						radio_index = radio_index+1
				else:
					current_topic = "general knowledge"
					self.generate_content(self.gen_prompt(name,current_topic,reminder,duration))
					self.tts_q.put("_song_.")
					self.segment_done.wait()
					self.segment_done.clear()

		except Exception as e:
			print("error in radio broadcast loop",e)
		finally:
			if self.tts_q is not None:
				self.tts_q.put(None)
			self.tts_thread.join(timeout=5.0)
			self.intro_thread.join(timeout=5.0)
			print("radio broadcast ended for listener")
			logging.info(f"[{self.name}] | Broadcast ended for listener")
			
	
#------------------BLE functions----------------------
	

	async def scan(self,address):
		"""
		scans for BLE devices until known mac address is found
		"""
		while not self.stop_ch.is_set():
			bbc = {}  #stores found compatible ble peripherals
			print("BLE scanning...")
			
			def callback(d, adv_data):
				for mac in address:
					if d.address == mac:
						bbc[d.address] = (d,adv_data.rssi)   #get rssi of compatible devices 
						print(bbc[d.address])
			scanner = BleakScanner(callback)
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
				logging.info(f"[{self.name}] | BLE Device found: {dev.name} - {dev.address}")
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
			print("Device ID not found in database")
			
			name = "Listener" 
			interests = ["General knowledge","Space","History"]
			reminders = ["drink water","water the plants"]	
		
		await asyncio.to_thread(self.play_radio,name,interests,reminders,"20 seconds")	#take blocking function and run it on seperate thread pool
		
		#coroutine stays open while client is connected	
		try:
			while client.is_connected:
				await asyncio.sleep(0.7)		
		except asyncio.CancelledError:
			print("listener info task cancelled...ble disconnect")
			raise
	
		
	async def notify_register(self,client):
		"""
		subscribe to BLE notification characteristic so movement status is received in real time
		"""
		
		#uart service callback function on bbc microbit
		def uart_callback(sender,data):
			self.status = data.decode("utf-8").strip()
			logging.info(f"[{self.name}] | movement status received: {self.status}")
			if self.status == "still":
				self.voice = self.voice1
				self.duration = "15 seconds"
			elif self.status == "move":
				self.voice = self.voice2
				self.duration = "10 seconds"
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
			#fresh state for each connection
			self.tts_q = queue.Queue()    															  
			self.intro_done = threading.Event()		  											      
			self.ble_disconnect = threading.Event()
			self.segment_done = threading.Event()
			self.audio_process = None
			task = None
			
			def on_dis(_):
				self.disconnect_time = time.time()
				logging.info(f"[{self.name}] | BLE disconnect event received")
				print("disconnect handler called")
				self.ble_disconnect.set()
				self.drain_tts_q()
				try:
					self.tts_q.put_nowait(None)
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
					ID = str(device.address)
					await self.notify_register(client)
					task = asyncio.create_task(self.start_broadcast(client,ID))
					try:
						await task
					except asyncio.CancelledError:
						pass
			except Exception as e:
				print("BLE connection Error",e)
			await asyncio.sleep(0.7)
			if not self.stop_ch.is_set():
				elapsed = (time.time() - self.disconnect_time)*1000
				logging.info(f"[{self.name}] | Returnig the BLE scanning : time since disconnect : {elapsed:.2f}ms")
	

	

		
	async def on_encoder_A_input(self, value: int):
		print("encoder A")

	async def on_encoder_B_input(self, value: int):
		print("encoder B")
		
		
