# channels/placeholder.py

import subprocess
import threading
import queue
import subprocess
import re
from ollama import generate
import wave
from piper import PiperVoice 
from datetime import datetime
import asyncio
from bleak import BleakClient,BleakScanner
import json

from base_channel import BaseChannel

class PlaceHolder(BaseChannel):
	def __init__(self, config):    									#constructor
		super().__init__(config)  									#call parent class constructor and pass it the same config
		
        # Get the directory where the current script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Relative folder "audio" inside the script directory
        self.audio_dir = config.get("audio_dir")
        
        #combine script location with folder name
		self.audio_dir = os.path.join(script_dir, self.audio_dir)
		self.voice = PiperVoice.load(os.path.join(self.audio_dir,"en_GB-semaine-medium.onnx"))   #fix up using yaml file later...
		
		
		self.tts_q = queue.Queue()    									#initialise tts queue - buffer between LLM and TTS threads
		self.intro_done = threading.Event()		  						#initialise threading event 
		self.ble_disconnect = threading.Event()
		
		self.audio_process = None
		self.audio_lock = threading.Lock()
		self.device_address = "F7:7D:A1:5F:95:D7","FD:37:9B:E6:BA:0A"

		print("loading llm model...")
		generate(model='llama3.2:3b',prompt="warmup",options={"num_predict":1})  #warmup model to reduce latency of loading it at first call
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
			print("BLE scanning loop starting...")
			self.stop_event.clear()
			self.ble_disconnect.clear()
			self.connect = asyncio.create_task(self.connectDevice())
			
		if self.magic_eye != None:
			if self.local:
				print(f"[{self.name}] Setting magic eye")
				
				self.magic_eye.send("local", self.encval)
			else:
				
				self.magic_eye.send("cloud", self.encval)
	
			
	async def stop(self):
		
		print(f"[{self.name}] Stopping")
		self.magic_eye.send(0xF000, 0)
	
	
	
	
#-----------Audio processing functions-------------------

	def initialise_audio(sample_rate=16000, channels=1):
		global audio_process
	
		with audio_lock:     
			if audio_process and audio_process.poll() is None:			#check is process (ffplay) object has been created and is still running
				return
	
		#start new ffplay process - similar method as other radio channels in existing system
		audio_process = subprocess.Popen(["ffplay", "-f", "s16le","-ar",str(sample_rate),"-nodisp","-autoexit","-"],
			stdin=subprocess.PIPE,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL)



#function to send raw PCM bytes to ffplay				
	def write_audio(audio_bytes):
		if audio_process and audio_process.stdin:
			try:
				audio_process.stdin.write(audio_bytes)
				audio_process.stdin.flush()              #forces pipe to ssend data immediately to prevent buffering delays
			except (BrokenPipeError, ValueError):
				pass

#function to stop audio and shutdown process cleanly					
	def stop_audio():
		global audio_process
		with audio_lock:
			if audio_process:
				try:
					audio_process.stdin.close()   
				except Exception:
					pass
				audio_process.wait()
				audio_process = None




#--------------TTS functions-------------------------
	def tts():
		print("TTS called")
		while True:
			text = tts_q.get()    #waits until text is in the tts queue
			if text is None:
				break
			print(text)
			for chunk in voice.synthesize(text):	#generate audio in chunks using piper
				if audio_process is None:
					initialise_audio(sample_rate=chunk.sample_rate,channels=chunk.sample_channels or 1)	
				#print(chunk)
				write_audio(chunk.audio_int16_bytes)  #call audio process write function with chunk (raw PCM bytes as input)
			tts_q.task_done()                         #mark task as complete


#----------------LLM functions----------------------


		
	def generate_content(prompt):
	
		buffer = ""  #before used to store partial text during streaming
		print("generating content...")
	
	#stream = generate(model='llama3.2:3b',prompt=prompt,context=context,keep_alive="1h",stream=True)
	#stream = generate(model='tinyllama',prompt=prompt,context=context,keep_alive="1h",stream=True)
		stream = generate(model='llama3.2:3b',prompt=prompt,keep_alive="1h",stream=True)   #streaming is set so tokens arrive progressively, the generate functions returns a generator of chunks
		for chunk in stream:
			if ble_disconnect.set():
				print("Radio stopped")
				break
			text = chunk.get("response","")             #returns token text from chunk
	#	print("content generated ",text)
			buffer = buffer + text                     #add text to buffer
			sentences,buffer = split_sentence(buffer)       #call function to detect when sentence has been complete
			for s in sentences:
				intro_done.wait()               #wait for introduction to be done to send sentence than send to tts qeueu
				tts_q.put(s.strip())
	
	#handle leftover text in buffer after loop		
		if buffer.strip():
			intro_done.wait()
			tts_q.put(buffer.strip())
		print("generation complete")
		


	
#function to split buffer into sentences and return remaining parts of sentences				
	def split_sentence(text):
		parts = re.split(r'([.?!])',text)   #text split at punctuation that occurs at the end of sentences [text,punctuation,text,punctuation...]
		sentences = []
	
		for i in range(0, len(parts)-1, 2):       #increment by 2 as every second item in parts is the text
			sentences.append(parts[i]+parts[i+1])    #include sentence and punctuation
		
	   #handling leftover text
	
		rem = parts[-1] if len(parts) % 2 else ""  
		return sentences,rem			
			

#function for introduction when bluetooth is intiailly connected to system ie. person's presence is detected	
	def intro():
		intro_done.clear() 
	#play intro music - can be replaced with downloaded wav file but ensure sample rate is compatible with ffmpeg
		print("calling intro")
		with wave.open("intro_22050.wav", "rb") as w:
			fs = w.getframerate()
			channels = w.getnchannels()
			if audio_process is None:
				initialise_audio(sample_rate=fs,channels=channels)
			
			while True:
				data = w.readframes(4096)
				if not data:
					break
				write_audio(data)

	#initialise_audio(sample_rate=fs,channels=channels)		
	
	#get current date information
		now = datetime.now()
		weekday = now.strftime("%A")
		month = now.strftime("%B")
		day = now.strftime("%d")
		year = now.strftime("%Y")

		if day[(len(day)-1)]=="1":
			day = day + "st"
		elif day[(len(day)-1)]=="2":
			day = day + "nd"
		else:
			day = day + "th"
		
	#introduction message	
		intro = [f"Hello and welcome to the Artificial Intelligence Radio Channel, Where we bring you important daily reminders and personalised content!",
		"I'm your host, and I've got a great lineup for you today.",
		f"Today is {weekday} the {day} of {month} {year}.", 
		"Stay tuned while we locally generate your content"]
	
	#feed each line of the inroduction into the tts queue	
		for line in intro:
			tts_q.put(line)
	
		intro_done.set()    #set intro complete event
	
	
#function to inject segment prompt with user information
	def gen_prompt(name,interest,reminder,duration):
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
	def intro_prompt(name,interest,reminder,duration):
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


	
	def play_radio(name,interests,reminder,duration):
		print("playing radio")
		tts_thread = threading.Thread(target=tts,daemon=True)
		tts_thread.start()
		intro_thread = threading.Thread(target=intro, daemon=True)
		intro_thread.start()
	
		#while not ble_disconnect.is_set():
		radio_index = 0
		print("starting segment : ") 
		generate_content(intro_prompt(name,interests,reminder,duration))
		while True:	
		#increment through interests for each radio segment generated
	
			if radio_index<len(interests):
				print("segment : ",radio_index) 
				generate_content(gen_prompt(name,interests[radio_index],reminder,duration))
				radio_index = radio_index+1
			else:
				print("segment : ",radio_index) 
				generate_content(gen_prompt(name,"general knowledge",reminder,duration))
				radio_index  = radio_index+1

	
	
		
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
		
	async def connectDevice(self):
		while True:
			device = await scan(device_address)
			task = None
			def on_dis(_):
				print("disconnect handler called")
				ble_disconnect.set()
				tts_q.put(None)
				if task:
					print("task cancelled")
					task.cancel()
			try:
				print("Connecting to BLE device...")
				async with BleakClient(device.address, disconnected_callback=on_dis) as client:
					print("connected to device\n")
					ble_disconnect.clear()
					
					ID = await Get_info(device,client)
					task = asyncio.create_task(play_seg(client,ID))
					try:
						await task
					except asyncio.CancelledError:
						pass
			except Exception as e:
				print("Error",e)
			print("disconnected")
			await asyncio.sleep(0.7)
			
			
	async def play_seg(client,ID):
		print("Task started")
		with open('users.json','r') as file:	
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
		await asyncio.to_thread(play_radio,name,interests,reminders,"20 seconds")		
		try:
			while client.is_connected:
				await asyncio.sleep(0.7)		
		except asyncio.CancelledError:
			print("ble disconnect.......")
			raise
	
	
		
	async def Get_info(device,client):
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
		
		
