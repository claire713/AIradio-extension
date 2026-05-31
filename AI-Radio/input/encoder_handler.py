import asyncio
from gpiozero import RotaryEncoder, Button
import random

from base_channel import InputEventNum



class EncoderHandler:
    def __init__(self, config: dict):
        self._queue = asyncio.Queue()

        self.config = config
        self.encoder_type = config["encoder_mapping"]["type"]
        self.pin_mapping = config["pin_mapping"]
        self.counterA = 0

        
        # Define GPIO pins
        self.ENCA_CLK = config["pin_mapping"]["enc_A_clk"]
        self.ENCA_DT = config["pin_mapping"]["enc_A_dir"]
        self.ENCA_SW = config["pin_mapping"]["enc_A_sw"]

        self.ENCB_CLK = config["pin_mapping"]["enc_B_clk"]
        self.ENCB_DT = config["pin_mapping"]["enc_B_dir"]
        self.ENCB_SW = config["pin_mapping"]["enc_B_sw"]
        

        print(self.ENCA_CLK)
        


        if self.encoder_type  == 1:
            self.encoderA = RotaryEncoder(a=self.ENCA_CLK, b=self.ENCA_DT, max_steps=1)
            self.button = Button(self.ENCA_SW, bounce_time=0.1)
            # Register callbacks to add events to the async queue
            self.encoderA.when_rotated = self._on_rotate_A
            self.button.when_pressed = self._on_button_press

            self.encoderB = RotaryEncoder(a=self.ENCB_CLK, b=self.ENCB_DT, max_steps=1)
            self.encoderB.when_rotated = self._on_rotate_B

            

    def _on_rotate_A(self):
        self._queue.put_nowait((InputEventNum.ENC_A, self.encoderA.steps))
     #   self._queue.put_nowait(("rotateA", self.encoderA.steps))

    def _on_rotate_B(self):
        self._queue.put_nowait((InputEventNum.ENC_B, self.encoderB.steps))
  #     self._queue.put_nowait(("rotateB", self.encoderB.steps))


    def _on_button_press(self):
        self._queue.put_nowait(("button", "pressed"))

  


    async def listen(self, callback):
        while True:
#            await asyncio.sleep(1)  # Simulated encoder input
            event_type, value = await self._queue.get()
            await callback(event_type, value)
