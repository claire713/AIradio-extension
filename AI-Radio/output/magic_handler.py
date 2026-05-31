import asyncio
import serial_asyncio

class MagicEyeProtocol(asyncio.Protocol):
    def __init__(self, on_data_callback=None):
        super().__init__()
        self.on_data_callback = on_data_callback
        self.transport = None
        self.buffer = bytearray()
        

    def connection_made(self, transport):
        self.transport = transport
        port = transport.serial.port
        print(f"[MagicEyeProtocol] Connected to {port}")

    def data_received(self, data):
        """Called whenever new data arrives from the serial port"""
        self.buffer.extend(data)

        # Example: process line-based responses ending in newline
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            line = line.decode(errors="ignore").strip()
            #print(f"[MagicEyeProtocol] Received: {line}")
            if self.on_data_callback:
                self.on_data_callback(line)

    def connection_lost(self, exc):
        print(f"[MagicEyeProtocol] Connection lost: {exc}")


class MagicEyeHandler:
    def __init__(self, config: dict):
        self._queue = asyncio.Queue()
        self.name = "MagicEye"
        self.config = config
        self.port = config["settings"]["magic_com"]
        self.last_cmd_type = -1
        self.last_value = -1
        self.transport = None
        self.protocol = None
        self.keypress = ""
        self.last_keypress = ""

        print(f"[{self.name}] Magic Eye Port: {self.port}")



    def send(self, cmd_type, value):
        """Queue a command to send, skip if identical to last."""
        if cmd_type == self.last_cmd_type and value == self.last_value:
            return
        self.last_cmd_type = cmd_type
        self.last_value = value
        self._queue.put_nowait((cmd_type, value))

    async def open(self):
        """Open the serial port asynchronously."""
        loop = asyncio.get_running_loop()
        self.transport, self.protocol = await serial_asyncio.create_serial_connection(
            loop,
            lambda: MagicEyeProtocol(on_data_callback=self.handle_response),
            self.port,
            baudrate=115200
        )
        print(f"Serial port {self.port} opened at 115200 baud")


   
    async def process(self):
        """Continuously send commands from the queue."""
        while True:
            try:
                cmd_type, cmd_value = await self._queue.get()

                if cmd_value == 0:
                    command = "]"
                    
                else:
                    command = f"[{cmd_type},{cmd_value}]"

                self.transport.write(command.encode("ascii"))
                print(f"[{self.name}] Sent: {command}")

                await asyncio.sleep(0.01)  # pacing
            except Exception as e:
                print(f"[{self.name}] Error in process loop: {e}")
                await asyncio.sleep(1)

    async def process_switches(self, callback):
        """Continuously send commands from the queue."""
        while True:
            try:
                command = f"I"

                self.transport.write(command.encode("ascii"))
                #print(f"[{self.name}] Sent: {command}")
                await asyncio.sleep(0.25)  # poll at 4Hz
              
                if self.keypress != "": # and  self.keypress != self.last_keypress:
                    await callback(self.keypress)
                    await asyncio.sleep(1.0)  
                    self.last_keypress = self.keypress
                    self.keypress = ""


            except Exception as e:
                print(f"[{self.name}] Error in process_switch loop: {e}")
                await asyncio.sleep(1)

    button_map = {1: "A",2: "B",3: "C",4: "L",5: "E",6: "F"}
    

    def parse_button(self, line: str) -> str:
        """Parse a line like 'I=2' and return the mapped character."""
        if not line.startswith("I="):
            return ""

        try:
            value = int(line[2:])
            if value == 0:
                 return ""           

            button = 1
            while value > 0:
                if value & 1:  # check lowest bit
                    return self.button_map.get(button, "?")  # return '?' if not mapped
                value >>= 1
                button += 1



        except ValueError:
            return ""

        return ""



    def handle_response(self, line: str):
        """Callback for handling responses from the device."""
        self.keypress = self.parse_button(line)
        #print(f"[{self.name}] Response: {line} = {self.keypress}")
