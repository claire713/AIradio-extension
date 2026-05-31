import asyncio
import sys

class KeyboardHandler:
    async def listen(self, callback):
        if not sys.stdin.isatty():
            print("No TTY attached. Skipping keyboard input.")
            return

        while True:
            key = await asyncio.to_thread(input, "Press key: ")  # Replace with proper async keyboard lib
            await callback(key.upper())
