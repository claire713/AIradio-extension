# base_channel.py

from abc import ABC, abstractmethod
from enum import IntEnum

import asyncio
import os
import subprocess


class InputEventNum(IntEnum):
    NONE = 0
    ENC_A = 1
    ENC_B = 2
    BUTTON_1 = 3
    BUTTON_2 = 3
    BUTTON_3 = 3
    BUTTON_4 = 3
    BUTTON_5 = 3
    BUTTON_6 = 3
    BUTTON_7 = 3
    BUTTON_8 = 3
    BUTTON_9 = 3
    BUTTON_10 = 3

class ChannelState(IntEnum):
    STOPPED = 0
    RUNNING = 1
    PAUSED = 2
    ERROR = 3


class BaseChannel(ABC):
    def __init__(self, config: dict):
        self.name = config["name"]
        self.config = config
        self.state = ChannelState.STOPPED
        self.encoder_A_value = 0
        self.encoder_B_value = 0
        self.requires_internet = config.get("requires_internet", False)
        self.auto_start = config.get("auto_start", False)
        self.magic_colour = config.get("magic_colour", 0xFFFFFF)
        self.magic_eye = None
        self.image = config.get("image", None)
        self.output_dir = os.path.join(os.getcwd(), "output")


    
    def enable_magic(self, magic_eye):
        self.magic_eye = magic_eye
        print(f"[{self.name}] Enabling Magic Eye: {self.magic_eye}")    

    async def run(self):
        try:
            while True:
                if self.state == ChannelState.RUNNING:
                    await self.play()
                await asyncio.sleep(1.0)
        except Exception as e:
            self.state = ChannelState.ERROR
            await self.on_error(e)

    async def toggle(self):
        if self.state == ChannelState.RUNNING:
            self.state = ChannelState.STOPPED
            await self.stop()
        else:
            self.state = ChannelState.RUNNING
            if self.image != None:
                print(f"[{self.name}] Displaying ", self.output_dir, self.image)
                await asyncio.create_subprocess_exec("python3", "display.py",  f"{self.image}",
                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL, cwd=self.output_dir )


    async def handle_encoder_A(self, value: int):
        self.encoder_A_value = value
        await self.on_encoder_A_input(value)

    async def handle_encoder_B(self, value: int):
        self.encoder_B_value = value
        await self.on_encoder_B_input(value)



    @abstractmethod
    async def stop(self):
        """called to stop the running audio."""
        pass

    @abstractmethod
    async def play(self):
        """Called repeatedly while channel is in RUNNING state."""
        pass

    @abstractmethod
    async def on_encoder_A_input(self, value: int):
        """Called when encoder sends new input."""
        pass

    @abstractmethod
    async def on_encoder_B_input(self, value: int):
        """Called when encoder sends new input."""
        pass


    async def on_error(self, error: Exception):
        print(f"[{self.name}] Error: {error}")
