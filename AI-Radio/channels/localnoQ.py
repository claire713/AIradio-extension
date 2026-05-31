
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
											
		self.intro_done = None	  						
		self.ble_disconnect = None
		self.stop_ch = threading.Event()
		self.audio_process = None
		self.audio_lock = threading.Lock()
		
		self.intro_thread = None
		self.segment_done = None

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
			if self.ble_disconnect is not None:
				self.ble_disconnect.clear()
			if self.intro_done is not None:
				self.intro_done.clear()
			if self.segment_done is not None:
				self.segment_done.clear()
			print("starting BLE scanning task")
			self.connect = asyncio.create_task(self.connectDevice())
	
	
	async def stop(self):
		"""
		Stop Channel cleanly by signaling thread exits,stopping audio,cancelling tasks,draining qeueus
		"""
		print(f"[{self.name}] Stopping")
		self.stop_ch.set()
		
		#Close ffplay subprocesses
		self.stop_audio()
		
		#unblock threads waiting on ble disconnect and finishing intro
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.intro_done is not None:
			self.intro_done.set()
		if self.segment_done is not None:
			self.segment_done.set()
	
			#self.tts_q.put(None)
		    #self.clear_tts_q(self.tts_q)
	
		
		
		#cleanup threads in background

		if self.intro_thread is not None and self.intro_thread.is_alive():
			self.intro_thread.jooin(timeout=5.0)
			print("waiting for intro thread to exit")
		
		#kill asyncio BLE task
		if self.connect is not None and not self.connect.done():
			self.connect.cancel()
			try:
				await self.connect
			except asyncio.CancelledError:
				pass
					
		self.magic_eye.send("clear",self.encval)
		print(f"[{self.name}] Stopped")
	
	
	
	
#------------------Audio processing functions-------------------------

	def initialise_audio(self,sample_rate=22050, channels=1):
		"""
		starts an ffplay process to consume raw PCM audio on stdin
		"""
		print(sample_rate)
		with self.audio_lock:     
			if self.audio_process and self.audio_process.poll() is None:			#check is process (ffplay) object has been created and is still running
				return
		
		try:
			self.audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)
		
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

#------------------TTS functions-------------------------
	def tts(self,text):
		"""
		Consume text items from tts qeueu. Text is synthesised by piper and streamed directly to ffplay as raw PCM chunks
		"""
		print("TTS called")	
		print(text)
		try:		
			if text is None:
				print("no text")
				return
				
			for chunk in self.voice.synthesize(text):
				if self.audio_process is None:
					self.initialise_audio()	
				self.write_audio(chunk.audio_int16_bytes)  #call audio process write function with chunk (raw PCM bytes as input)
		except Exception as e:
			print("TTS worker error - continued",e)
			

			

	def play_song(self):
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

#------------------LLM functions----------------------


		
	def generate_content(self,prompt):
		"""
		Stream LLM output from ollama API and push sentences to TTS qeueu
		"""
			
		buffer = ""  						   # buffer used to store partial text during streaming
		print("generating content...")

		try:
			for chunk in  generate(model='llama3.2:3b',prompt=prompt,keep_alive=-1,stream=True) :
				if self.ble_disconnect.is_set():
					print("Generation interupted by BLE disconnect")
					break
		
				text = chunk.get("response","")             #returns token text from chunk

				
				buffer = buffer + text                     #add text to buffer
				sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
				for s in sentences:
					print("sentence ready - waiting for intro complete")
					self.intro_done.wait()
					self.tts(s.strip())
		
			if buffer.strip():
				self.intro_done.wait()
				self.tts(buffer.strip())
			print("generation complete")
		except Exception as e:
			print("error gen content",e)			
		

			
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
						self.write_audio(data)
		except Exception:
			print("error playing wav file")
		print("intro complete")
		self.intro_done.set()    #set intro complete event
	
	
	
#------------------prompt functions----------------------	
	
	
	def gen_prompt(self,name,interest,reminder,duration):
		"""
		Inject segment prompt with user information for segments about user interests
		"""
		
		seg_prompt = f""" a spoken radio-style segment that sounds like a segment of an ongoing broadcast.
Include truthful facts about the listen's interests. relate the tone to the listen'r movement status.
Do not include sound effects or notes. Only use ASCII Characters. Do not use smart quotes or special symbols
Listener's name : {name}
Listener's interest : {interest}
movement status : {self.status}
Reminder to subtly include in the segment:{reminder}
Length: approximately 100 words
"""
		return seg_prompt


	def intro_prompt(self,name,interest,reminder,duration):
		"""
		Inject segment prompt with user information for first generated segment providing an overview of all of the users interests
		"""
		seg_prompt = f"""You are a radio presenter on the "Local Artificial Intelligence Radio Channel" speaking to a single listener.
Your task : Generate a spoken radio-style introduction segment for an ongoing broadcast.
This segment is responsible for giving the listener their daily reminders and informing them about what the next segments will be talking about.
Do not include sound effects or notes. Only use ASCII Characters. Do not use smart quotes or special symbols
Listener's name : {name}
Listener's interest : {interest}
movement status : {self.status}
Reminder to subtly include in the segment:{reminder}
Length: approximately 70 words
"""
		return seg_prompt



#------------------Radio Orchestration functions----------------------

	def play_radio(self,name,interests,reminder,duration):
		"""
		Scheduling of radio broadcast for BLE connected listener
		"""
		print("playing radio")
	
		self.intro_thread = threading.Thread(target=self.intro, daemon=False,name=f"intro")
		self.intro_thread.start()

	
		radio_index = 0
		try:
			while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
				if radio_index<len(interests):
					self.generate_content(self.gen_prompt(name,interests[radio_index],reminder,duration))
					self.play_song()
					self.segment_done.wait()
					self.segment_done.clear()
					radio_index = radio_index+1
				else:
					current_topic = "general knowledge"
					self.generate_content(self.gen_prompt(name,current_topic,reminder,duration))
					self.play_song()
					self.segment_done.wait()
					self.segment_done.clear()
		except Exception as e:
			print("error in radio broadcast loop",e)
		finally:
			
			self.intro_thread.join(timeout=5.0)
			print("radio broadcast ended for listener")
			
	
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
			self.intro_done = threading.Event()		  											      
			self.ble_disconnect = threading.Event()
			self.segment_done = threading.Event()
			task = None
			
			def on_dis(_):
				print("disconnect handler called")
				self.ble_disconnect.set()
	
				self.stop_audio()
				if task and not task.done():
					task.cancel()
			try:
				print("Connecting to BLE device...")
				async with BleakClient(device.address, disconnected_callback=on_dis) as client:
					print("connected to device\n")
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
	

	

		
	async def on_encoder_A_input(self, value: int):
		print("encoder A")

	async def on_encoder_B_input(self, value: int):
		print("encoder B")
		
		
