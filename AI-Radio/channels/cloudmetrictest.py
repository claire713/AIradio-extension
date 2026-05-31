
"""
Add comments about overall system
  - name: Clodud
    class: channels.cloud.Cloud
    magic_colour: 0xFFF
    auto_start: false
    button: C
    requires_internet: true
    audio_dir: audio/ai_seg
    llm_model: "llama3.2:3b"
    device_address:
      - "F7:7D:A1:5F:95:D7"
      - "FD:37:9B:E6:BA:0A"
    ble_scan_interval: 5.0
    ble_reconnect_delay: 0.7

"""

	
import psutil
import csv
import time
import argparse
from datetime import datetime
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
from datetime import datetime
from bleak import BleakClient,BleakScanner
from queue import Empty
from base_channel import BaseChannel
from dotenv import load_dotenv
from openai import OpenAI
from openai import AsyncOpenAI
import logging

_UART_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
_AUDIO_CHUNK = 4096
SONG_CMD = object()
STOP = object()



class Cloud(BaseChannel):
	def __init__(self, config):   
		"""
		Initialise local channel
		"""
		
		super().__init__(config)  		#call parent class (Base channel) constructor 
		
        #--find absolute path to audio folder adn env file relative to script location--
		script_dir = os.path.dirname(os.path.abspath(__file__))
		self.audio_dir = os.path.join(script_dir,config.get("audio_dir","audio"))
		key_path = os.path.join(self.audio_dir , ".env")  					
        
        #--BLE configuration--
		mac_addresses = config.get("device_address",[])
		if isinstance(mac_addresses, str):       #accept single string and tuple of mac addresses from config file
			mac_addresses = [mac_addresses]
		self.device_address: tuple[str,...] = tuple(mac_addresses)
  
		#--LLM, voice and song retrieval from config file--
		self.llm_model = config.get("llm_model")
		self.relax = config.get("relax_songs")
		self.upbeat = config.get("upbeat_songs")
		self.voice1 = config.get("chill_voice")
		self.voice2 = config.get("upbeat_voice")
		self.voiceprompt1 = "Energetic Irish radio broadcaster.Upbeat, crisp, and motivating."
		self.voiceprompt2 = "Energetic Irish radio broadcaster.Upbeat, crisp, and motivating."
		self.voiceprompt = self.voiceprompt2 
		self.voice = self.voice1
		
		
			
		#--Initialise concurrency and state objects--
		self.relax = config.get("relax_songs")
		self.upbeat = config.get("upbeat_songs")
		self.stop_ch = asyncio.Event()
		self.audio_lock = threading.Lock()
		self.connect = None
		self.tts_q = None
		self.intro_done = None
		self.ble_disconnect = None
		self.segment_done = None
		self.schedule_audio_task = None
		self.intro_task = None


		#--Mode state--
		self.status = "still"
		self.duration = "15 seconds"
		self.encval = 100
		self.segment = True
		self.relax_song = 0
		self.upbeat_song = 0
	

		#--Get API key--
		load_dotenv(key_path)
		self.API_key = os.getenv("API_KEY")
		


	async def play(self):
		"""
		Send magic eye signal to display local icon and start BLE task
		"""
		self.magic_eye.send("cloud",self.encval)
		if self.connect is None or self.connect.done():
			self.stop_ch.clear()
			logging.info(f"[{self.name}] | Channel activated")
			logging.info(f"[{self.name}] | Cloid icon set ")
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

		
	# Drain queue and send None stop sentinel to unblock tts()
		if self.tts_q is not None:
			self.drain_tts_q()
			await self.tts_q.put(STOP)
			logging.info(f"[{self.name}] | Stop command sent to TTS queue")
		
		if self.intro_task is not None and not self.intro_task.done():
			self.intro_task.cancel()
			try:
				await self.intro_task
			except asyncio.CancelledError:
				pass
			logging.info(f"[{self.name}] | intro task cancelled")
		if self.schedule_audio_task is not None and not self.schedule_audio_task.done():
			self.schedule_audio_task.cancel()
			try:
				await self.schedule_audio_task 
			except asyncio.CancelledError:
				pass
			logging.info(f"[{self.name}] | audio scheduling task cancelled")	
		
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
		print(sample_rate)
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
	async def schedule_audio(self):
		"""
		Consume text items from tts qeueu. Text is synthesised by piper and streamed directly to ffplay as raw PCM chunks
		"""
		print("TTS called")
		
		while True:
			text = await self.tts_q.get()    #waits until text is in the tts queue 
			try:
				if text is SONG_CMD:
					await self.play_song()
					self.segment_done.set()
					logging.info(f"[{self.name}] | Song complete, segment_done set")
					continue
				if text is STOP:
					logging.info(f"[{self.name}] | TTS stop sentinel received")
					return
				await self.synthesise(text)
			except asyncio.CancelledError:
				raise
			except Exception:
				logging.exception(f"[{self.name}] | audio schedule error")
			finally:
				self.tts_q.task_done()

					
	async def synthesise(self,text):
		if self.stop_ch.is_set() or self.ble_disconnect.is_set():
			return
		try:
			async with self.AIclient.audio.speech.with_streaming_response.create(model="gpt-4o-mini-tts", voice=self.voice,input=text,instructions=self.voiceprompt,response_format="pcm",) as response:
				async for chunk in response.iter_bytes(1024):
					if self.stop_ch.is_set() or self.ble_disconnect.is_set():
						break
					if self.audio_process is None:
						self.initialise_audio()	
					self.write_audio(chunk)  #call audio process write function with chunk (raw PCM bytes as input)
		except Exception as e:
			print("TTS  error - continued",e)

	async def play_song(self):
		if self.status == "active":
			song = self.upbeat[self.upbeat_song]
			self.upbeat_song = (self.upbeat_song + 1) % len(self.upbeat)
		else:
			song = self.relax[self.relax_song]
			self.relax_song = (self.relax_song + 1) % len(self.relax)
		
		song_path = os.path.join(self.audio_dir, song)
		logging.info(f"[{self.name}] | Playing song: {song}")
		with wave.open(song_path, "rb") as w:
			while True:
				data = w.readframes(_AUDIO_CHUNK)
				if not data:
					break
				self.write_audio(data)
			
			
	def drain_tts_q(self):
		try:
			while True:
				self.tts_q.get_nowait()
				self.tts_q.task_done()
		except asyncio.QueueEmpty:
			pass	
				
	
	async def generate_content(self,prompt):
		"""
		Stream LLM output from openAI API and push sentences to TTS qeueu
		"""
		
		t_start = time.perf_counter()
		ttft = None
		completion_tokens = 0
		prompt_tokens = 0
		buffer = ""  						   # buffer used to store partial text during streaming
		try:
			stream = await self.openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            stream_options={"include_usage": True}  # gives token counts in final chunk
        )

			async for chunk in stream:
            # Capture usage from final chunk
				if chunk.usage is not None:
					completion_tokens = chunk.usage.completion_tokens
					prompt_tokens = chunk.usage.prompt_tokens

				if not chunk.choices:
					continue

				delta = chunk.choices[0].delta.content or ""

				# Capture time to first token
				if ttft is None and delta:
					ttft = time.perf_counter() - t_start

				buffer = buffer + delta
				sentences, buffer = self.split_sentence(buffer)
				for s in sentences:
					await self.intro_done.wait()
					await self.tts_q.put(s.strip())

		except Exception as e:
			print("error generating content", e)

		if buffer.strip():
			await self.intro_done.wait()
			await self.tts_q.put(buffer.strip())

		t_end = time.perf_counter()
		total_time = t_end - t_start
		tokens_per_second = completion_tokens / total_time if total_time > 0 else 0

		#print(f"[LLM] TTFT={ttft:.3f}s | total={total_time:.2f}s | tokens={completion_tokens} | {tokens_per_second:.1f} tok/s")
		#logging.info(f"[{self.name}] | LLM metrics — TTFT={ttft:.3f}s total={total_time:.2f}s tokens={completion_tokens} tok/s={tokens_per_second:.1f}")
		self._save_llm_log("cloud", ttft, total_time, prompt_tokens, completion_tokens, tokens_per_second)



	def _save_llm_log(self, system, ttft, total_time, prompt_tokens, completion_tokens, tokens_per_second):
		"""Log LLM performance metrics to CSV for thesis evaluation."""
		log_path = os.path.join(self.audio_dir, "llm_metrics.csv")
		file_exists = os.path.isfile(log_path)
		with open(log_path, "a", newline="",encoding="utf-8",errors="replace") as f:
			writer = csv.writer(f)
			if not file_exists:
				writer.writerow(["timestamp", "system", "ttft_s", "total_time_s", "prompt_tokens", "completion_tokens", "tokens_per_second"])
			writer.writerow([
				datetime.now().isoformat(),
				system,                         # "local" or "cloud"
				round(ttft, 4) if ttft else "",
				round(total_time, 3),
				prompt_tokens,
				completion_tokens,
				round(tokens_per_second, 2)])
							


	
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
		#function for introduction when bluetooth is intiailly connected to system ie. person's presence is detected, runs thread,sets intro_done when complete
		"""
		
		self.intro_done.clear() 
	
		print("calling intro")
		
		intro_files = [
		os.path.join(self.audio_dir,"intro_22050.wav"),		
		os.path.join(self.audio_dir,"cloud_intro.wav")
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
		except Exception as e:
			print("error playing wav file",e)
		finally:
			print("intro complete")
			logging.info(f"[{self.name}] | Intro audio bytes written to ffplay")
			self.intro_done.set()    #set intro complete event
	
	
	
#------------------prompt functions----------------------	
	
	
	def gen_prompt(self,name,interest,reminder,duration):
		"""
		Inject segment prompt with user information for segments about user interests
		"""
		
		seg_prompt = f"""You are a knowledgeble radio presenter about {interest}, speaking on the "Cloud Artificial Intelligence Radio Channel" to a single listener.
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
Greet {name} by name, reflect on their current movement status {self.status}, and subtly include their reminders: {reminder}.
Acknowledge whats coming up on the show : {interests}. Information should feel natural and conversational, not like a list.
Do not include sound effects or notes. Keep it warm and engaging. Only use ASCII Characters. Only use commas, letters and fullstops.
Length: approximately 70 words
"""
		return seg_prompt

	#------------------Radio Orchestration functions----------------------

	async def play_radio(self,name,interests,reminder,duration):
		"""
		Scheduling of radio broadcast for BLE connected listener
		"""
		print("playing radio")
		self.intro_task = asyncio.create_task(self.intro(), name="intro")
	
		self.schedule_audio_task = asyncio.create_task(self.schedule_audio(), name="tts_worker")
		
		radio_index = 0
		intro = False
		try:
			while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
				if radio_index<len(interests):
					if intro == False:
						await self.generate_content(self.intro_prompt(name,interests,reminder,duration))
						await self.tts_q.put(SONG_CMD)
						await self.segment_done.wait()
						self.segment_done.clear()
						intro = True
					else:
						await self.generate_content(self.gen_prompt(name,interests[radio_index],reminder,duration))
						await self.tts_q.put(SONG_CMD)
						await self.segment_done.wait()
						self.segment_done.clear()
						radio_index = radio_index+1
				else:
					await self.generate_content(self.gen_prompt(name, "general knowledge", reminder, duration))
					await self.tts_q.put(SONG_CMD)
					await self.segment_done.wait()
					self.segment_done.clear()
					
		except asyncio.CancelledError:
			raise
		except Exception:
			logging.exception(f"[{self.name}] | Error in broadcast loop")
		finally:
			if self.tts_q is not None:
				await self.tts_q.put(STOP)
			await asyncio.gather(self.schedule_audio_task, self.intro_task,return_exceptions=True)
			logging.info(f"[{self.name}] | Broadcast ended for {name}")
	
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
		await self.play_radio(name, interests, reminders, "20 seconds")
		
		
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
			status = data.decode("utf-8").strip()
			logging.info(f"[{self.name}] | movement status received: {self.status}")
			if status == "still":
				self.status = "still while listening"
				self.voice = self.voice1
			elif self.status == "move":
				self.status = "moving while listening"
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
			self.tts_q = asyncio.Queue()
			self.intro_done = asyncio.Event()
			self.ble_disconnect = asyncio.Event()
			self.segment_done = asyncio.Event()
			self.audio_process = None
			self.AIclient = AsyncOpenAI(api_key=self.API_key)
			task = None
			
			def on_dis(_):
				self.disconnect_time = time.time()
				logging.info(f"[{self.name}] | BLE disconnect event received")
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
		
		
