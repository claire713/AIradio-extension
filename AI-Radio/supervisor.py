
import importlib
import asyncio
from input.keyboard_handler import KeyboardHandler
from input.encoder_handler import EncoderHandler
from output.magic_handler import MagicEyeHandler
from base_channel import ChannelState, InputEventNum
import logging
from overlay import GenOverlay
from spot import Spot
import subprocess
import os

class Supervisor:
    def __init__(self, config):
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        file_handler = logging.FileHandler("test_log.txt")
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s | %(message)s', datefmt='%H:%M:%S')
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        print("Handlers attached:")
        logging.info("[Supervisor] | Logging initialised")
        self.name = "Supervisor"
        self.config = config
        self.channels = {}
        self.encoder_map = {}
        self.keyboard = KeyboardHandler()
        #self.encoder = EncoderHandler(config)
        self.magic = MagicEyeHandler(config)
        self.output_dir = os.path.join(os.getcwd(), "output")
        self.banner_image = config["settings"]["image"]

        name, ext = self.banner_image.rsplit(".", 1)
        self.banner_image_ = f"{name}_." + ext

 


        self._load_channels()
        
        self.encoderA=0
        self.encoderB=0
        self.encoder_type = config["encoder_mapping"]["type"]
        
        #check for autostart flag in yaml file, if present auto start the first channel with it set
        
        
        self.volume = config["settings"]["volume"]
        subprocess.run(["amixer", "set", "Master", self.volume])
      

       

        if self.banner_image!= None:
	    #add ip address to banner!!
            GenOverlay(self.output_dir, self.banner_image, self.banner_image_)

            print(f"[{self.name}] Displaying ", self.output_dir, self.banner_image_)
            subprocess.run(["python3", "display.py",  f"{self.banner_image_}"], capture_output=True, text=True, cwd=self.output_dir )
        else:
            print(f"[{self.name}] Unable to Display ", self.output_dir, self.banner_image)

            


        #play startup sound.... 
        subprocess.run(["ffplay", "-nodisp", "-autoexit",  "start.mp3"], capture_output=True, text=True )

        #for ch_config in self.config["channels"]:
        #    auto_start = ch_config.get("auto_start","")
        #    if auto_start != None:
        #        if auto_start == True:
        #            print("Running auto start...")
        #            ch_config.toggle()
        

            #if ch.state.value == ChannelState.RUNNING and name != except_name:
            #    await ch.toggle()


 

        
        

# Inside Supervisor class

   

    def _load_channels(self):
        self.channels = {}
        self.key_to_channel = {}

        for ch_config in self.config["channels"]:
            module_path, class_name = ch_config["class"].rsplit(".", 1)
            module = importlib.import_module(module_path)
            channel_class = getattr(module, class_name)

            name = ch_config["name"]
            button = ch_config.get("button", "").strip().upper()

            instance = channel_class(ch_config)
            instance.enable_magic(self.magic)

            self.channels[name] = instance
            if button:
                self.key_to_channel[button] = name
            logging.info(f"[Supervisor] | Channel Loaded: {name} mapped to key: {button}")
            
            
            

    async def run(self):
        await self.magic.open()

        for name, ch in self.channels.items():
            
            if ch.auto_start:
                print("auto starting channel", ch.name)
                await ch.toggle()
                break;


        tasks = [
            self.keyboard.listen(self._on_key),
          #  self.encoder.listen(self._on_encoder),
            *[ch.run() for ch in self.channels.values()],
            self.magic.process(),
            self.magic.process_switches(self._on_key)
        ]
        await asyncio.gather(*tasks)
        
    async def stop_all(self, except_name=None):
        for name, ch in self.channels.items():
            if ch.state.value == ChannelState.RUNNING and name != except_name:
                await ch.toggle()

    async def _on_key(self, key):
        #key = self._normalize_key(key)
        key = key.strip().upper().replace(" ", "")
        
        if key == "Q":
            print("Quitting: stopping all channels.")
            logging.info(f"[Supervisor | Quit signal received - stopping all channels")
            await self.stop_all()
            return
            
        if key == "+":
            self.encoderA=+1
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_A(self.encoderA) 
        if key == "-":
            self.encoderA=-1
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_A(self.encoderA) 

        if key == ".":
            self.encoderB=+1
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_B(self.encoderB) 
        if key == ",":
            self.encoderB=-1
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_B(self.encoderB)

        if key in self.key_to_channel:
            new_channel_name = self.key_to_channel[key]
            new_channel = self.channels[new_channel_name]

            # Stop currently running channels
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING and name != new_channel_name:
                   
                    await ch.toggle()  # Stop the current one
                    logging.info(f"[Supervisor] | Stopping channel: {name}")

            # Start new channel (or toggle it if it's already running)
            await new_channel.toggle()
          

            logging.info(f"[Supervisor] | Starting Channel: {new_channel_name}")

    async def _on_encoder(self, event_type, value):
        if event_type == InputEventNum.ENC_A: #"rotateA":
            #print(f"Rotary encoder A moved: {value}")
            #self.magic.send("enc A", value)
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_A(value)
                    break 

        elif event_type == InputEventNum.ENC_B: #"rotateB":
            #print(f"Rotary encoder B moved: {value}")
            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    await ch.handle_encoder_B(-value) 
                    break

        elif event_type == InputEventNum.BUTTON_1: #"button":
	    #rotate around channels on button press!!!!! 
            #quit the current channel and go to the next!
            #print("Button pressed!")
            running = False

            for name, ch in self.channels.items():
                if ch.state.value == ChannelState.RUNNING:
                    running = True
                   

            if not running:
               #print("requesting first channel to start play ")
               await self.channel_list[0].toggle()
               self.channel_index = 0
            else:
            
                if self.channel_index == len(self.channel_list)-1:
                    await self.channel_list[self.channel_index].toggle()
                else:
                    #print("playing next")
                    await self.channel_list[self.channel_index].toggle() #stop current
                    self.channel_index = self.channel_index+1
                    await self.channel_list[self.channel_index].toggle() #start next... 

