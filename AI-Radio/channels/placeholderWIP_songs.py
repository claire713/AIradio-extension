# channels/placeholder.py


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

class PlaceHolder(BaseChannel):
	def __init__(self, config):    #constructor    									#constructor
		super().__init__(config)  									#call parent class constructor and pass it the same config
		
        # Get the directory where the current script is located
		script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Relative folder "audio" inside the script directory
		self.audio_dir = config.get("audio_dir")
        
        #combine script location with folder name
		self.audio_dir = os.path.join(script_dir, self.audio_dir)
		self.voice = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-semaine-medium.onnx"))   #fix up using yaml file later...
	
		self.connect = None
		self.tts_q = None 									#initialise tts queue - buffer between LLM and TTS threads
		self.intro_done = None	  						#initialise threading event 
		self.ble_disconnect = None
		self.stop_ch = threading.Event()
		self.audio_process = None
		self.audio_lock = threading.Lock()
		self.device_address = "F7:7D:A1:5F:95:D7","FD:37:9B:E6:BA:0A"
		print("loading llm model...")
		generate(model='llama3.2:3b',prompt="warmup",keep_alive=-1,options={"num_predict":1})  #warmup model to reduce latency of loading it at first call
		self.indexA = 0
		self.max_indexA = 3
		self.indexB = 0
		self.max_indexB = 3
		self.modeA = ["AI Companion", "Reminders", "Interests", "Favourite hits"]
		self.modeB = ["100% local", "hybrid1", "hybrid2", "100% cloud"]
		self.encval = 100
		self.local = True
		
	
	async def play(self):
		#print(f"[{self.name}] Play Starting")
		if self.connect is None or self.connect.done():
			self.stop_ch.clear()
			#self.ble_disconnect.clear()
			print("state = BLE scanning")
			self.connect = asyncio.create_task(self.connectDevice())
	
	
	

#-----------Audio processing functions-------------------

	def initialise_audio(self,sample_rate=16000, channels=1):
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
			for chunk in self.voice.synthesize(text):	#generate audio in chunks using piper
				if self.audio_process is None:
					self.initialise_audio(sample_rate=chunk.sample_rate,channels=chunk.sample_channels or 1)	
				#print(chunk)
				self.write_audio(chunk.audio_int16_bytes)  #call audio process write function with chunk (raw PCM bytes as input)
			self.tts_q.task_done()                         #mark task as complete


	def clear_tts_q(self,q):
		try:
			while True:
				q.get_nowait()
				q.task_done()
		except Empty:
				pass

#----------------LLM functions----------------------


		
	def generate_content(self,prompt,out=None,out_list=None,intro_wait=True):
	
		buffer = ""  #before used to store partial text during streaming
		print("generating content...")
	
		#stream = generate(model='llama3.2:3b',prompt=prompt,context=context,keep_alive="1h",stream=True)
		stream = generate(model='llama3.2:3b',prompt=prompt,keep_alive="1h",stream=True)
		#stream = generate(model='tinyllama',prompt=prompt,context=context,keep_alive="1h",stream=True)
		#stream = generate(model='llama3.2:3b',prompt=prompt,stream=True)   #streaming is set so tokens arrive progressively, the generate functions returns a generator of chunks
		for chunk in stream:
			if self.ble_disconnect.is_set():
				print("Radio stopped")
				break
			text = chunk.get("response","")             #returns token text from chunk
			print("content generated ",text)
			buffer = buffer + text                     #add text to buffer
			sentences,buffer = self.split_sentence(buffer)       #call function to detect when sentence has been complete
			for s in sentences:
				#self.intro_done.wait()               #wait for introduction to be done to send sentence than send to tts qeueu
				#self.tts_q.put(s.strip())
				print(type(s),s)
				sentence = s
				if not sentence:
					continue
				if intro_wait and self.intro_done is not None:
					self.intro_done.wait()
				
				if out is not None:
					out.put(buffer.strip(sentence))
				elif outlist is not None:
					outlist.append(sentence)
				
		#handle leftover text in buffer after loop		
		if buffer.strip():
			#self.intro_done.wait()
			self.tts_q.put(buffer.strip())
			if intro_wait and self.intro_done is not None:
				self.intro_done.wait()
				
			if out is not None:
				out.put(buffer.strip(sentence))
			elif outlist is not None:
				outlist.append(sentence)

		
		print("generation complete")
		
	def play_song(self):
		intro_content = os.path.join(self.audio_dir,"relax_jazz.wav")
		self.stop_audio()
		music_process = subprocess.Popen(["ffplay", "-nodisp","-autoexit","-loglevel","quiet",intro_content],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
		return process 	
	    	
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
		intro_content = os.path.join(self.audio_dir,"Welcome_intro.wav")
		with wave.open(intro_music, "rb") as w:
			fs = w.getframerate()
			channels = w.getnchannels()
			if self.audio_process is None:
				self.initialise_audio(sample_rate=fs,channels=channels)
			
			while True:
				data = w.readframes(4096)
				if not data:
					break
				self.write_audio(data)
		with wave.open(intro_content, "rb") as w:
			fs = w.getframerate()
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
		seg_prompt = f"""You are a radio presenter on the the "Local Artificial Intelligence Radio Channel" speaking to a single listener.
Your task : Generate a spoken radio-style segment that sounds like a segment of an ongoing broadcast.
You are the only host speaking in the segment 
Do not include sound effects or notes, only the content that will be spoken
You should speak as humanly as possible
Address the listener by name throughout the segment but do not ask any questions
Use simple, clear sentence suitable for spoken audio
Don't make any assumptions about the listener. Focus on delivering truthful facts and reminders.
Include host personality
Listener's name : {name}
Listener's interests : {interest}
Reminder to subtly include in the segment:{reminder}
Length: approximately {duration}
"""
		return seg_prompt

#function to inject intro prompt with user information
	def intro_prompt(self,name,interest,reminder,duration):
		intro_prompt = f"""You are a radio presenter on the the "Local Artificial Intelligence Radio Channel" speaking to a single listener.
Your task : Generate a spoken radio-style segment that sounds like an introduction part of an ongoing broadcast.
This segment is responsible for giving the listener their daily reminders and informing them about what the next segments will be talking about
You are the only host speaking in the segment 
Do not include sound effects or notes, only the content that will be spoken
You should speak as humanly as possible
Address the listener by name throughout the segment but do not ask any questions
Use simple, clear sentence suitable for spoken audio
Don't make any assumptions about the listener. Focus on delivering Truthful facts and reminders.
Include host personality
Listener's name : {name}
segment topics : {interest}
Reminders: {reminder}
Length: approximately {duration}
"""
		return intro_prompt
	
	def play_radio(self,name,interests,reminder,duration):
		print("playing radio")
		tts_thread = threading.Thread(target=self.tts,daemon=True)
		tts_thread.start()
		intro_thread = threading.Thread(target=self.intro, daemon=True)
		intro_thread.start()
	
		#while not ble_disconnect.is_set():
		radio_index = 0
		print("starting segment : ") 
		self.generate_content(self.intro_prompt(name,interests,reminder,duration),out=self.tts_q,intro_wait=True,)
		#while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
		self.tts_q.join()
		#increment through interests for each radio segment generated
		while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
			if radio_index<len(interests):
				current_topic = interests[radio_index]
				print("segment : ",radio_index) 
			else:
				current_topic = "general knowledge"
			self.generate_content(self.gen_prompt(name,interests[radio_index],reminder,duration),out=self.tts_q,intro_wait=False,)
			self.tts_q.join()
		
			next_index = radio_index + 1
			if next_index<len(interests):
				next_topic = interests[next_index]
				print("segment : ",radio_index) 
				radio_index = radio_index+1
			else:
				next_topic = "general knowledge"
			
			next_sentences = []
			
			gen_thread = threading.Thread(target = self.generate_content,args = (self.gen_prompt(name,interests[next_index],reminder,duration),),kwargs={out_list:next_sentences,intro_wait:False},daemon=True,)
			song = self.play_song()
			song.wait()
			gen_thread.join()
			for sentence in next_sentences:
				self.tts_q.put(sentence)
			radio_index = radio_index+1
			self.tts_q.join()
		
		
	async def scan(self,address):
		while True:
			print("BLE scanning...")
			dev = await BleakScanner.discover(timeout=5)
			for d in dev:
				print(d.name,d.address)
				for mac in address:
					if d.address == mac:
						print("Device found")
						return d
			await asyncio.sleep(0.7)			
	
			
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
		id_resp = asyncio.Event()
		user = {"id":None}
	
		#uart service callback function on bbc microbit
		def uart_callback(sender,data):
			user["id"] = data.decode()
			id_resp.set()
	
		#enable indications for uart service
		await client.start_notify("6e400002-b5a3-f393-e0a9-e50e24dcca9e",uart_callback)
		#wait for id to be sent to central device
		await id_resp.wait()
		#stop notifications
		await client.stop_notify("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
		return user["id"]
			
	async def stop(self):
		print(f"[{self.name}] Stopping")
		self.stop_ch.set()
		if self.ble_disconnect is not None:
			self.ble_disconnect.set()
		if self.intro_done is not None:
			self.intro_done.set()
		if self.tts_q is not None:
			self.clear_tts_q(self.tts_q)
			try:
				self.tts_q.put_nowait(None)
			except Exception:
				pass
		self.stop_audio()
		
		if self.connect and not self.connect.done():
			self.connect.cancel()
			
		self.ble_disconnect.set()
		self.clear_tts_q(self.tts_q)
		self.tts_q.put(None)
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
		if self.local:
			#self.magic_eye.send("cloud",self.encval)
			self.local = False
		else:
			#self.magic_eye.send("local",self.encval)
			self.local = True
		if (self.indexB > self.max_indexB):
			self.indexB = 0
		elif (self.indexB < 0):
				self.indexB = self.max_indexB
		print(f"[{self.name}] Encoder B Mode {self.modeB[self.indexB]}")
		
		
