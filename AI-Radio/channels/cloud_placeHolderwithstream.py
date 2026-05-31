# channels/placeholder.py
#WIP


from dotenv import load_dotenv
from openai import OpenAI
import subprocess
import threading
import queue
import subprocess
import re
import os
from ollama import generate
import wave
from piper import PiperVoice 
from datetime import datetime
import asyncio
from bleak import BleakClient,BleakScanner
import json
from base_channel import BaseChannel
from queue import Empty
import sys
sys.stdout.reconfigure(encoding='utf-8')

class CloudPlaceHolder(BaseChannel):
	def __init__(self, config):    #constructor    									#constructor
		super().__init__(config)  									#call parent class constructor and pass it the same config
		
        # Get the directory where the current script is located
		script_dir = os.path.dirname(os.path.abspath(__file__))
		env_path = os.path.join(script_dir, ".env")
        # Relative folder "audio" inside the script directory
		self.audio_dir = config.get("audio_dir")
          
        #combine script location with folder name
		self.audio_dir = os.path.join(script_dir, self.audio_dir)

	
		key_path = os.path.join(self.audio_dir , ".env")
	
		self.connect = None
		self.tts_q = None 									#initialise tts queue - buffer between LLM and TTS threads
		self.intro_done = None	  						#initialise threading event 
		self.ble_disconnect = None
		self.stop_ch = threading.Event()
		self.audio_process = None
		self.song_process = None
		self.audio_lock = threading.Lock()
		self.device_address = "F7:7D:A1:5F:95:D7","FD:37:9B:E6:BA:0A"
		self.indexA = 0
		self.max_indexA = 3
		self.indexB = 0
		self.max_indexB = 3
		self.voice2 = 'nova'
		self.voice1 = 'alloy'
		self.status = 'still'
		self.voiceprompt1 = "speak like a radio host giving a broadcast"
		self.voiceprompt2 = "speak like a radio host giving a broadcast"
		self.modeA = ["AI Companion", "Reminders", "Interests", "Favourite hits"]
		self.modeB = ["100% local", "hybrid1", "hybrid2", "100% cloud"]
		self.encval = 100
		self.segment = True
		load_dotenv(key_path)
		API_key = os.getenv("API_KEY")
		self.voiceprompt = self.voiceprompt1 
		self.voice = self.voice1
		audio_lock = threading.Lock()
		if not API_key:
			raise ValueError("API_KEY not found in environment variables.")
		self.openai = OpenAI(api_key = API_key)
		
		
	async def play(self):
		#print(f"[{self.name}] Play Starting")
		self.magic_eye.send("cloud",self.encval)
		if self.connect is None or self.connect.done():
			self.stop_ch.clear()
			#self.ble_disconnect.clear()
			print("state = BLE scanning")
			self.connect = asyncio.create_task(self.connectDevice())
	
	
	

#-----------Audio processing functions-------------------

	def initialise_audio(self,sample_rate=24000, channels=1):
		#global self.audio_process
	
		with self.audio_lock:     
			if self.audio_process and self.audio_process.poll() is None:			#check is process (ffplay) object has been created and is still running
				return
		
		#print("starting new ffplay process")
		#start new ffplay process - similar method as other radio channels in existing system
		self.audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)



#function to send raw PCM bytes to ffplay				
	def write_audio(self,audio_bytes):
		if self.audio_process and self.audio_process.stdin:
			try:
				#print("writing audio data")
				self.audio_process.stdin.write(audio_bytes)
				self.audio_process.stdin.flush()              #forces pipe to ssend data immediately to prevent buffering delays
			except (BrokenPipeError, ValueError):
				pass

#function to stop audio and shutdown process cleanly					
	def stop_audio(self):
		#global self.audio_process
		with self.audio_lock:
			if self.audio_process:
				try:
					if self.audio_process.stdin:
						self.audio_process.stdin.close()   
				except Exception:
					pass
				try:
					self.audio_process.wait(timeout=0.2)
				except Exception:
					try:
						self.audio_process.kill()
					except Exception:
						pass
				self.audio_process = None

#--------------TTS functions-------------------------
	def tts(self):
		print("TTS called")
		while True:
			text = self.tts_q.get()    #waits until text is in the tts queue
			if text is None:
				break
			print(text)
			with self.openai.audio.speech.with_streaming_response.create(model="gpt-4o-mini-tts", voice=self.voice,input=text,instructions=self.voiceprompt,response_format="pcm",) as response:
				for chunk in response.iter_bytes(1024):
					if self.audio_process is None:
						self.initialise_audio()	
					self.write_audio(chunk)  #call audio process write function with chunk (raw PCM bytes as input)
			self.tts_q.task_done()                         #mark task as complete                       #mark task as complete
			
			
	def clear_tts_q(self,q):
		try:
			while True:
				q.get_nowait()
				q.task_done()
		except Empty:
				pass

#----------------LLM functions----------------------


		
	def generate_content(self,prompt):
		buffer = ""  #before used to store partial text during streaming
		print("generating content...")
 #streaming is set so tokens arrive progressively, the generate functions returns a generator of chunks
		stream = self.openai.responses.create(model="gpt-5",input=prompt,stream=True) 
		for event in stream:
			if self.ble_disconnect.is_set():
				print("Radio stopped")
				break
			if event.type == "response.output_text.delta":
				text = event.delta
				buffer = buffer + text
				sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
				for s in sentences:
					print(s)
					self.intro_done.wait()               #wait for introduction to be done to send sentence than send to tts qeueu
					self.tts_q.put(s.strip())
			if event.type == "response.completed":
				break
	#handle leftover text in buffer after loop		
		if buffer.strip():
			self.intro_done.wait()
			self.tts_q.put(buffer.strip())
		print("generation complete")
			
	    	
#function to split buffer into sentences and return remaining parts of sentences				
	def split_sentence(self,text):
		parts = re.split(r'([.?!])',text)   #text split at punctuation that occurs at the end of sentences [text,punctuation,text,punctuation...]
		sentences = []
	
		for i in range(0, len(parts)-1, 2):       #increment by 2 as every second item in parts is the text
			sentences.append(parts[i]+parts[i+1])    #include sentence and punctuation
		
	   #handling leftover text
	
		rem = parts[-1] if len(parts) % 2 else ""  
		return sentences,rem			
			

#function for introduction when bluetooth is intiailly connected to system ie. person's presence is detected	
	def intro(self):
		self.intro_done.clear() 
		
	#play intro music - can be replaced with downloaded wav file but ensure sample rate is compatible with ffmpeg
		print("calling intro")
		intro_music = os.path.join(self.audio_dir,"intro_22050.wav")
	
		with wave.open(intro_music, "rb") as w:
			fs = 24000
			channels = w.getnchannels()
			if self.audio_process is None:
				self.initialise_audio(sample_rate=fs,channels=channels)
			
			while True:
				data = w.readframes(4096)
				if not data:
					break
				self.write_audio(data)

		
	#	self.play_song()
		self.intro_done.set()    #set intro complete event
	
	
#function to inject segment prompt with user information
	def gen_prompt(self,name,interest,reminder,duration):
		seg_prompt = f"""You are a radio presenter on the the "cloud Artificial Intelligence Radio Channel" speaking to a single listener.
Your task : Generate a spoken radio-style segment that sounds like a segment of an ongoing broadcast.
You are the only host speaking in the segment. Mention the persons movement status 
Do not include sound effects or notes. Only use ASCII Characters. Do not use smart quotes or special symbols
Listener's name : {name}
Listener's interests : {interest}
movement status : {self.status}
Reminder to subtly include in the segment:{reminder}
Length: approximately 20 seconds
"""
		return seg_prompt

	
	def play_radio(self,name,interests,reminder,duration):
		print("playing radio")
		tts_thread = threading.Thread(target=self.tts,daemon=True)
		tts_thread.start()
		intro_thread = threading.Thread(target=self.intro, daemon=True)
		intro_thread.start()
	
		#while not ble_disconnect.is_set():
		radio_index = 0
		while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
			if radio_index<len(interests):
				self.generate_content(self.gen_prompt(name,interests[radio_index],reminder,duration))
				radio_index = radio_index+1
			else:
				current_topic = "general knowledge"
				self.generate_content(self.gen_prompt(name,current_topic,reminder,duration))
			
	

	async def scan(self,address):
		while True:
			bbc = {}
			print("BLE scanning...")
			def callback(d, adv_data):
				for mac in address:
					if d.address == mac:
						bbc[d.address] = (d,adv_data.rssi)
						print(d.name,adv_data.rssi)
			scanner = BleakScanner(callback)
			await scanner.start()
			await asyncio.sleep(5)
			await scanner.stop()
			
			if bbc:
				s = max(bbc,key = lambda b:bbc[b][1])
				dev = bbc[s][0]
				print("chosen : ",dev.name)
				return dev			
	
			
	async def get_info(self,client,ID):
		print("Task started")
		db_file = os.path.join(self.audio_dir,"users.json")
		with open(db_file,'r') as file:	
			data = json.load(file)
		if ID in data["users"]:
			print("User present")
			user = data["users"][ID]
			name = user["name"]
			interests = [user["interest1"],user["interest2"],user["interest3"]]
			reminders = user["reminder1"],user["reminder2"]		
		else:
			print("user not found")
			name = "N/A" 
			interests = "General" 
			reminders = "no reminders"	
		await asyncio.to_thread(self.play_radio,name,interests,reminders,"20 seconds")		
		try:
			while client.is_connected:
				await asyncio.sleep(0.7)		
		except asyncio.CancelledError:
			print("ble disconnect.......")
			raise
		
	async def Get_id(self,device,client):
		print("retrieving user id")
		user = {"id":str(device.address)}
		
		#uart service callback function on bbc microbit
		def uart_callback(sender,data):
			self.status = data.decode("utf-8").strip()
			if self.status == "still":
				self.voice = self.voice1
				self.voiceprompt = self.voiceprompt1
				self.duration = "15 seconds"
			elif self.status == "move":
				self.voice = self.voice2
				self.voiceprompt = self.voiceprompt2
				self.duration = "10 seconds"
			print("status updated...",self.status)
	
		#enable indications for uart service
		await client.start_notify("6e400002-b5a3-f393-e0a9-e50e24dcca9e",uart_callback)
		return user["id"]
			
	async def stop(self):
		print(f"[{self.name}] Stopping")
		self.stop_ch.set()
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.intro_done is not None:
			self.intro_done.set()
		if self.tts_q is not None:
			self.tts_q.put(None)
			self.clear_tts_q(self.tts_q)
			try:
				self.tts_q.put_nowait(None)
			except Exception:
				pass
		self.stop_audio()
		
		if self.connect and not self.connect.done():
			self.connect.cancel()
			
		
		
		self.connect.done()
		self.magic_eye.send(0xF000, 0)
	
		
	async def connectDevice(self):
		while not self.stop_ch.is_set():
			device = await self.scan(self.device_address)
			self.tts_q = queue.Queue()    															  
			self.intro_done = threading.Event()		  											      
			self.ble_disconnect = threading.Event()
			task = None
			def on_dis(_):
				print("disconnect handler called")
				self.ble_disconnect.set()
				self.clear_tts_q(self.tts_q)
				self.tts_q.put(None)
				self.stop_audio()
				if task:
					print("task cancelled")
					task.cancel()
			try:
				print("Connecting to BLE device...")
				async with BleakClient(device.address, disconnected_callback=on_dis) as client:
					#will add functionality for multiple connections
					print("connected to device\n")
					self.ble_disconnect.clear()
					ID = await self.Get_id(device,client)
					print("ID retrieved : ",ID)
					task = asyncio.create_task(self.get_info(client,ID))
					try:
						await task
					except asyncio.CancelledError:
						pass
			except Exception as e:
				print("Error",e)
			print("disconnected")
			await asyncio.sleep(0.7)		
	

	

		
	async def on_encoder_A_input(self, value: int):
		self.indexA = self.indexA + value
		#self.encval = self.encval + 10
		if (self.indexA > self.max_indexA):
			self.indexA = 0
			#self.encval = 0
		elif (self.indexA < 0):
			self.indexA = self.max_indexA
			#self.encval = 40
		print(f"[{self.name}] Encoder A Mode {self.modeA[self.indexA]}")

	async def on_encoder_B_input(self, value: int):
		self.magic_eye.send(0xF000, 0)
		print("switching to songs")
		if self.segment:
			self.stop_ch.set()
			if self.tts_q:
				self.clear_tts_q(self.tts_q)
				try:
					self.tts_q.put_nowait(None)
				except:
					pass
			self.stop_audio
			#self.magic_eye.send("cloud",self.encval)
		else:
			#self.magic_eye.send("local",self.encval)
			self.segment = True
		if (self.indexB > self.max_indexB):
			self.indexB = 0
		elif (self.indexB < 0):
				self.indexB = self.max_indexB
		print(f"[{self.name}] Encoder B Mode {self.modeB[self.indexB]}")
		
		
