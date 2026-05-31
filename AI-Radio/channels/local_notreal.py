
"""
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

Architecture notes:
  - All I/O-bound operations (BLE, LLM streaming) run natively on the asyncio
    event loop using AsyncClient and Bleak.
  - CPU-bound operations (Piper ONNX inference, WAV file reads, ffplay stdin
    writes) are offloaded to the thread pool via asyncio.to_thread(), which is
    the documented Python mechanism for bridging CPU-bound blocking work into an
    async system without blocking the event loop.
  - threading.Event and queue.Queue are replaced with asyncio.Event and
    asyncio.Queue so all synchronisation uses a single concurrency model.
  - threading.Thread for tts and intro are replaced with asyncio tasks,
    managed via asyncio.create_task() and cancelled cleanly on stop.
"""

import json
import asyncio
import subprocess
import re
import os
import wave
from ollama import AsyncClient
from piper import PiperVoice
from bleak import BleakClient, BleakScanner
from base_channel import BaseChannel
import logging

_UART_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
_AUDIO_CHUNK = 4096

# Sentinel objects placed on the TTS queue to signal song playback and stop.
# object() instances are unique singletons — identity is checked with 'is',
# not '==', so there is no risk of accidental equality with any other value.
# This is the standard Python sentinel pattern used in the standard library.
_SONG_CMD = object()
_STOP = object()


class Local(BaseChannel):

    def __init__(self, config):
        """
        Initialise Local channel. Loads Piper voice models and sets initial
        state. asyncio primitives (Event, Queue) are created per-connection
        inside connectDevice() so state is fresh on each BLE reconnect.
        """
        super().__init__(config)

        # -- Absolute path to audio assets relative to this script --
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.audio_dir = os.path.join(script_dir, config.get("audio_dir", "audio"))

        # -- Piper TTS voice models --
        # PiperVoice.load() is blocking (reads ONNX model from disk) but only
        # runs once at startup so it is acceptable here.
        # Additional voices: github.com/OHF-Voice/piper1-gpl
        self.voice1 = PiperVoice.load(os.path.join(self.audio_dir, "en_GB-semaine-medium.onnx"))
        self.voice2 = PiperVoice.load(os.path.join(self.audio_dir, "en_GB-northern_english_male-medium.onnx"))
        self.voice = self.voice1  # default voice

        # -- BLE configuration --
        mac_addresses = config.get("device_address", [])
        if isinstance(mac_addresses, str):
            mac_addresses = [mac_addresses]
        self.device_address: tuple[str, ...] = tuple(mac_addresses)

        # -- LLM and song config --
        self.llm_model = config.get("llm_model")
        self.relax = config.get("relax_songs")
        self.upbeat = config.get("upbeat_songs")

        # -- Persistent asyncio stop signal --
        # stop_ch is created once and reused across connections. All other
        # asyncio primitives are recreated per-connection in connectDevice().
        self.stop_ch = asyncio.Event()

        # -- Per-connection state (initialised in connectDevice) --
        self.connect = None
        self.tts_q = None
        self.intro_done = None
        self.ble_disconnect = None
        self.segment_done = None
        self.tts_task = None
        self.intro_task = None

        # -- Audio subprocess state --
        # audio_lock is still a threading.Lock because write_audio and
        # stop_audio are called from asyncio.to_thread() workers which run
        # on real OS threads. A threading.Lock correctly serialises access
        # between those workers.
        self.audio_process = None
        self.audio_lock = __import__('threading').Lock()

        # -- Mode state --
        self.status = "still"
        self.duration = "15 seconds"
        self.encval = 100
        self.relax_song = 0
        self.upbeat_song = 0


    # ------------------------------------------------------------------ #
    #  Channel lifecycle                                                   #
    # ------------------------------------------------------------------ #

    async def play(self):
        """
        Display local icon on magic eye and start BLE scanning task.
        Called by the channel manager when this channel is selected.
        """
        self.magic_eye.send("local", self.encval)
        if self.connect is None or self.connect.done():
            self.stop_ch.clear()
            logging.info(f"[{self.name}] | Channel activated")
            if self.ble_disconnect is not None:
                self.ble_disconnect.clear()
            if self.intro_done is not None:
                self.intro_done.clear()
            if self.segment_done is not None:
                self.segment_done.clear()
            logging.info(f"[{self.name}] | Starting BLE scanning task")
            self.connect = asyncio.create_task(self.connectDevice())


    async def stop(self):
        """
        Stop channel cleanly:
          1. Signal all coroutines and tasks to exit via stop_ch.
          2. Unblock any coroutines waiting on asyncio events.
          3. Drain TTS queue and send None stop sentinel.
          4. Cancel asyncio tasks (tts, intro, broadcast, connectDevice).
          5. Stop ffplay subprocess.
          6. Clear magic eye display.
        """
        logging.info(f"[{self.name}] | Stop signal received")
        self.stop_ch.set()

        # Unblock any coroutine awaiting these events
        if self.ble_disconnect is not None:
            self.ble_disconnect.set()
        if self.intro_done is not None:
            self.intro_done.set()
        if self.segment_done is not None:
            self.segment_done.set()

        # Drain queue and send None stop sentinel to unblock tts()
        if self.tts_q is not None:
            await self._drain_tts_q()
            try:
                self.tts_q.put_nowait(_STOP)
                logging.info(f"[{self.name}] | Stop sentinel sent to TTS queue")
            except asyncio.QueueFull:
                logging.warning(f"[{self.name}] | TTS queue full — stop sentinel not sent")

        # Cancel TTS and intro asyncio tasks
        for task, name in [(self.tts_task, "TTS"), (self.intro_task, "Intro")]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                logging.info(f"[{self.name}] | {name} task cancelled")

        # Cancel top-level BLE task
        if self.connect is not None and not self.connect.done():
            self.connect.cancel()
            try:
                await self.connect
            except asyncio.CancelledError:
                pass
            logging.info(f"[{self.name}] | BLE task cancelled")

        # Stop ffplay — runs in thread pool because it uses blocking subprocess calls
        await asyncio.to_thread(self.stop_audio)

        self.magic_eye.send("clear", self.encval)
        logging.info(f"[{self.name}] | Channel stopped, icon cleared")


    # ------------------------------------------------------------------ #
    #  Audio subprocess (ffplay)                                          #
    # ------------------------------------------------------------------ #

    def initialise_audio(self, sample_rate=22050, channels=1):
        """
        Start an ffplay subprocess that consumes raw s16le PCM on stdin.
        Called from within asyncio.to_thread() workers — blocking is safe here.
        """
        with self.audio_lock:
            if self.audio_process and self.audio_process.poll() is None:
                return  # already running
            try:
                self.audio_process = subprocess.Popen(
                    ["ffplay", "-f", "s16le", "-ar", str(sample_rate),
                     "-ac", str(channels), "-nodisp", "-autoexit", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logging.info(f"[{self.name}] | ffplay started — {sample_rate}Hz {channels}ch")
            except FileNotFoundError:
                logging.error(f"[{self.name}] | ffplay not found — install ffmpeg")
            except Exception:
                logging.exception(f"[{self.name}] | Failed to start ffplay")


    def write_audio(self, audio_bytes):
        """
        Write raw PCM bytes to ffplay stdin.
        Called from asyncio.to_thread() workers — blocking is safe here.
        stdin.flush() ensures bytes are passed to ffplay immediately,
        preventing buffering-induced audio gaps.
        """
        if self.audio_process and self.audio_process.stdin:
            try:
                self.audio_process.stdin.write(audio_bytes)
                self.audio_process.stdin.flush()
            except (BrokenPipeError, ValueError):
                logging.warning(f"[{self.name}] | Audio write skipped — ffplay pipe closed")


    def stop_audio(self):
        """
        Close ffplay stdin and wait for process exit. If it does not exit
        within 0.5 s it is killed. Called via asyncio.to_thread() from stop().
        """
        with self.audio_lock:
            if self.audio_process is None:
                return
            try:
                if self.audio_process.stdin:
                    self.audio_process.stdin.close()
            except Exception:
                logging.exception(f"[{self.name}] | Error closing ffplay stdin")

            try:
                self.audio_process.wait(timeout=0.5)
                self.audio_process = None
            except subprocess.TimeoutExpired:
                try:
                    self.audio_process.kill()
                except Exception:
                    logging.exception(f"[{self.name}] | Could not kill ffplay")
                finally:
                    self.audio_process = None

            logging.info(f"[{self.name}] | ffplay stopped")


    # ------------------------------------------------------------------ #
    #  TTS pipeline                                                        #
    # ------------------------------------------------------------------ #

    async def tts(self):
        """
        Async task: consume items from tts_q and synthesise speech.

        Queue item types:
          str        — synthesise with Piper via asyncio.to_thread()
          SONG_CMD   — play a WAV song file via asyncio.to_thread()
          None       — stop sentinel, exits the loop

        Piper's synthesize() runs ONNX inference (CPU-bound). It is
        offloaded to the thread pool via asyncio.to_thread() so the event
        loop remains free for BLE callbacks and other coroutines between
        audio chunks.
        """
        logging.info(f"[{self.name}] | TTS task started")

        while True:
            text = await self.tts_q.get()  # yields control until item arrives

            try:
                if text is _SONG_CMD:
                    await self._play_song()
                    self.segment_done.set()
                    logging.info(f"[{self.name}] | Song complete, segment_done set")
                    continue

                if text is _STOP:
                    logging.info(f"[{self.name}] | TTS stop sentinel received")
                    return

                await self._synthesise(text)

            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(f"[{self.name}] | TTS error — continuing")
            finally:
                self.tts_q.task_done()


    async def _synthesise(self, text):
        """
        Offload Piper ONNX inference to the thread pool.
        synthesize() is CPU-bound so asyncio.to_thread() is the correct
        mechanism — it runs the call on a worker thread while the event
        loop continues handling BLE events.
        """
        def _run():
            chunks = list(self.voice.synthesize(text))
            return chunks

        if self.stop_ch.is_set():
            return

        chunks = await asyncio.to_thread(_run)

        for chunk in chunks:
            if self.stop_ch.is_set():
                break
            if self.audio_process is None:
                await asyncio.to_thread(
                    self.initialise_audio,
                    chunk.sample_rate,
                    chunk.sample_channels or 1
                )
            await asyncio.to_thread(self.write_audio, chunk.audio_int16_bytes)


    async def _play_song(self):
        """
        Select and stream a WAV song file to ffplay.
        File reads and audio writes are offloaded to the thread pool because
        wave.open() and write_audio() are blocking I/O operations.
        """
        if self.status == "active":
            song = self.upbeat[self.upbeat_song]
            self.upbeat_song = (self.upbeat_song + 1) % len(self.upbeat)
        else:
            song = self.relax[self.relax_song]
            self.relax_song = (self.relax_song + 1) % len(self.relax)

        song_path = os.path.join(self.audio_dir, song)
        logging.info(f"[{self.name}] | Playing song: {song}")

        def _stream_song():
            with wave.open(song_path, "rb") as w:
                while True:
                    data = w.readframes(_AUDIO_CHUNK)
                    if not data:
                        break
                    self.write_audio(data)

        await asyncio.to_thread(_stream_song)


    async def _drain_tts_q(self):
        """
        Discard all pending items in the TTS queue without processing them.
        Called during stop() to prevent the tts task consuming stale items
        after a disconnect.
        """
        while not self.tts_q.empty():
            try:
                self.tts_q.get_nowait()
                self.tts_q.task_done()
            except asyncio.QueueEmpty:
                break


    # ------------------------------------------------------------------ #
    #  Intro audio                                                         #
    # ------------------------------------------------------------------ #

    async def intro(self):
        """
        Async task: play pre-recorded WAV intro files when a listener connects.
        Sets intro_done on completion (or failure) so generate_content()
        is unblocked and can begin queuing sentences for TTS.

        The finally block guarantees intro_done is always set even if the
        WAV files are missing or corrupt — without this, generate_content()
        would block indefinitely on await self.intro_done.wait().
        """
        self.intro_done.clear()
        intro_files = [
            os.path.join(self.audio_dir, "intro_22050.wav"),
            os.path.join(self.audio_dir, "Welcome_intro.wav")
        ]
        logging.info(f"[{self.name}] | Intro audio started")

        try:
            for file in intro_files:
                if self.stop_ch.is_set():
                    break

                def _stream_intro(path):
                    with wave.open(path, "rb") as w:
                        fs = w.getframerate()
                        ch = w.getnchannels()
                        if self.audio_process is None:
                            self.initialise_audio(sample_rate=fs, channels=ch)
                        while True:
                            data = w.readframes(_AUDIO_CHUNK)
                            if not data:
                                break
                            self.write_audio(data)

                await asyncio.to_thread(_stream_intro, file)

        except Exception:
            logging.exception(f"[{self.name}] | Intro audio error")
        finally:
            self.intro_done.set()  # always unblock generate_content()
            logging.info(f"[{self.name}] | Intro complete, intro_done set")


    # ------------------------------------------------------------------ #
    #  LLM generation                                                      #
    # ------------------------------------------------------------------ #

    async def generate_content(self, prompt):
        """
        Stream tokens from Ollama using AsyncClient.generate() and push
        complete sentences to the TTS queue.

        AsyncClient uses httpx.AsyncClient under the hood — each token
        arrival is a genuine asyncio await point, so the event loop remains
        responsive to BLE events throughout generation.

        Sentences are detected incrementally via split_sentence() so TTS
        synthesis begins on the first sentence while the LLM is still
        generating the remainder — minimising perceived latency.

        generate_content() waits on intro_done before queuing each sentence
        so TTS does not begin until the intro WAV has finished playing,
        preventing audio overlap.
        """
        buffer = ""
        logging.info(f"[{self.name}] | Calling Ollama LLM ({self.llm_model})")

        try:
            client = AsyncClient()
            async for chunk in await client.generate(
                model=self.llm_model,
                prompt=prompt,
                keep_alive=-1,
                stream=True
            ):
                if self.ble_disconnect.is_set() or self.stop_ch.is_set():
                    logging.info(f"[{self.name}] | LLM generation interrupted")
                    break

                text = chunk.get("response", "")
                buffer += text
                sentences, buffer = self.split_sentence(buffer)

                for s in sentences:
                    # Wait for intro WAV to finish before queuing — prevents
                    # TTS audio overlapping with the intro playback.
                    await self.intro_done.wait()
                    await self.tts_q.put(s.strip())
                    logging.info(f"[{self.name}] | Sentence queued for TTS")

        except Exception:
            logging.exception(f"[{self.name}] | LLM generation error")

        # Flush any remaining partial sentence left in the buffer
        if buffer.strip():
            await self.intro_done.wait()
            await self.tts_q.put(buffer.strip())

        logging.info(f"[{self.name}] | LLM generation complete")


    def split_sentence(self, text):
        """
        Split streaming text buffer at sentence boundaries (.?!) and return
        complete sentences plus any remaining incomplete fragment.

        re.split with a capturing group returns:
          [text, punctuation, text, punctuation, ..., remainder]
        so we step through in pairs to reconstruct each sentence with its
        terminal punctuation intact.
        """
        parts = re.split(r'([.?!])', text)
        sentences = []
        for i in range(0, len(parts) - 1, 2):
            sentences.append(parts[i] + parts[i + 1])
        remainder = parts[-1] if len(parts) % 2 else ""
        return sentences, remainder


    # ------------------------------------------------------------------ #
    #  Prompt templates                                                    #
    # ------------------------------------------------------------------ #

    def gen_prompt(self, name, interest, reminder, duration):
        """
        Build a personalised radio segment prompt incorporating the listener's
        name, current movement status, reminders, and topic of interest.
        """
        return (
            f"You are a knowledgeable radio presenter about {interest}, "
            f"speaking on the 'Local AI Radio Channel' to a single listener.\n"
            f"Deliver a spoken, personalised radio-style factual segment.\n"
            f"Greet {name} by name, reflect their current movement status "
            f"({self.status}), and subtly include their reminders: {reminder}.\n"
            f"Weave in interesting facts about {interest}. Keep it natural and conversational.\n"
            f"Do not include sound effects or notes. Only use ASCII characters, "
            f"commas, letters and full stops.\n"
            f"Length: approximately 100 words.\n"
        )


    def intro_prompt(self, name, interests, reminder, duration):
        """
        Build a personalised intro segment prompt giving an overview of all
        listener interests for the first segment of the broadcast.
        """
        return (
            f"You are a radio presenter speaking on the 'Local AI Radio Channel' "
            f"to a single listener.\n"
            f"Deliver a spoken, personalised radio-style introduction segment.\n"
            f"Greet {name} by name, reflect their current movement status "
            f"({self.status}), and subtly include their reminders: {reminder}.\n"
            f"Acknowledge what is coming up on the show: {interests}. "
            f"Keep it natural and conversational.\n"
            f"Do not include sound effects or notes. Only use ASCII characters, "
            f"commas, letters and full stops.\n"
            f"Length: approximately 70 words.\n"
        )


    # ------------------------------------------------------------------ #
    #  Radio orchestration                                                 #
    # ------------------------------------------------------------------ #

    async def play_radio(self, name, interests, reminder, duration):
        """
        Orchestrate the radio broadcast for a connected listener.

        Launches tts() and intro() as concurrent asyncio tasks, then loops
        through the listener's interests generating LLM content for each.
        Each segment is followed by a song, coordinated via segment_done.

        On exit (stop signal, BLE disconnect, or exception) both tasks are
        cancelled cleanly via asyncio.gather().
        """
        logging.info(f"[{self.name}] | Broadcast starting for {name}")

        self.tts_task = asyncio.create_task(self.tts(), name="tts_worker")
        self.intro_task = asyncio.create_task(self.intro(), name="intro")

        radio_index = 0
        intro_done = False

        try:
            while not self.stop_ch.is_set() and not self.ble_disconnect.is_set():
                if radio_index < len(interests):
                    if not intro_done:
                        await self.generate_content(
                            self.intro_prompt(name, interests, reminder, duration)
                        )
                        await self.tts_q.put(_SONG_CMD)
                        await self.segment_done.wait()
                        self.segment_done.clear()
                        intro_done = True
                    else:
                        await self.generate_content(
                            self.gen_prompt(name, interests[radio_index], reminder, duration)
                        )
                        await self.tts_q.put(_SONG_CMD)
                        await self.segment_done.wait()
                        self.segment_done.clear()
                        radio_index += 1
                else:
                    await self.generate_content(
                        self.gen_prompt(name, "general knowledge", reminder, duration)
                    )
                    await self.tts_q.put(_SONG_CMD)
                    await self.segment_done.wait()
                    self.segment_done.clear()

        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(f"[{self.name}] | Error in broadcast loop")
        finally:
            # Send stop sentinel and cancel tasks cleanly
            if self.tts_q is not None:
                await self.tts_q.put(_STOP)
            await asyncio.gather(
                self.tts_task, self.intro_task,
                return_exceptions=True
            )
            logging.info(f"[{self.name}] | Broadcast ended for {name}")


    # ------------------------------------------------------------------ #
    #  BLE functions                                                       #
    # ------------------------------------------------------------------ #

    async def scan(self, address):
        """
        Repeatedly scan for BLE advertisements until a known MAC address is
        found. Selects the device with the strongest RSSI if multiple known
        devices are visible simultaneously.

        RSSI is used as a proximity heuristic — in an indoor environment
        multipath fading means RSSI is not a reliable absolute distance
        measure, but it is sufficient for selecting the closest of multiple
        known devices.
        """
        while not self.stop_ch.is_set():
            found = {}

            def callback(device, adv_data):
                if device.address in address:
                    found[device.address] = (device, adv_data.rssi)

            scanner = BleakScanner(callback)
            try:
                logging.info(f"[{self.name}] | BLE scan cycle started")
                await scanner.start()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(f"[{self.name}] | BLE scanner error")
                continue
            finally:
                try:
                    await scanner.stop()
                except Exception:
                    logging.warning(f"[{self.name}] | Error stopping BLE scanner")

            if found:
                best = max(found, key=lambda b: found[b][1])
                device = found[best][0]
                logging.info(f"[{self.name}] | BLE device found: {device.name} ({device.address})")
                return device

        return None


    async def start_broadcast(self, client, device_id):
        """
        Look up the listener profile by BLE MAC address from users.json and
        start the radio broadcast. Falls back to default profile if device ID
        is not registered.
        """
        db_file = os.path.join(self.audio_dir, "users.json")

        try:
            with open(db_file, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            logging.error(f"[{self.name}] | users.json not found at {db_file}")
            return
        except json.JSONDecodeError:
            logging.error(f"[{self.name}] | users.json is malformed")
            return

        if device_id in data.get("users", {}):
            user = data["users"][device_id]
            name = user["name"]
            interests = [user["interest1"], user["interest2"]]
            reminders = (user["reminder1"], user["reminder2"])
            logging.info(f"[{self.name}] | Listener profile loaded: {name}")
        else:
            logging.warning(f"[{self.name}] | Device {device_id} not in database — using defaults")
            name = "Listener"
            interests = ["General knowledge", "Space", "History"]
            reminders = ("drink water", "water the plants")

        # play_radio is now a native coroutine — no thread pool bridge needed
        await self.play_radio(name, interests, reminders, "20 seconds")


    async def notify_register(self, client):
        """
        Subscribe to the BBC micro:bit UART characteristic so movement status
        notifications are received in real time. Updates voice and duration
        based on whether the listener is still or moving.

        The UART service UUID (Nordic UART Service) is used because the
        micro:bit exposes accelerometer-derived movement state over BLE
        using this standard service profile.
        """
        def uart_callback(sender, data):
            status = data.decode("utf-8").strip()
            if status == "still":
                self.status = "still while listening"
                self.voice = self.voice1
                self.duration = "15 seconds"
            elif status == "move":
                self.status = "moving while listening"
                self.voice = self.voice2
                self.duration = "10 seconds"
            logging.info(f"[{self.name}] | Movement status updated: {self.status}")

        await client.start_notify(_UART_UUID, uart_callback)


    async def connectDevice(self):
        """
        Main BLE connection loop. Scans for a known device, connects, registers
        for notifications, and starts the broadcast. Reconnects automatically
        after disconnection or error.

        Fresh asyncio primitives are created for each connection so no stale
        state from a previous session can affect the new one.

        on_dis() is a synchronous BLE disconnect callback (Bleak requirement).
        It sets ble_disconnect and cancels the broadcast task. The closure
        captures `task` by reference — task is assigned after on_dis is
        defined but before it can fire, so the reference is always valid
        when the callback executes.
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
            task = None

            def on_dis(_):
                """
                Synchronous BLE disconnect callback (required by Bleak).
                Sets ble_disconnect event and schedules task cancellation on
                the event loop via call_soon_threadsafe, which is the correct
                way to interact with the event loop from a non-async context.
                """
                logging.info(f"[{self.name}] | BLE disconnected")
                self.ble_disconnect.set()
                self.stop_audio()
                if task is not None and not task.done():
                    task.cancel()

            try:
                logging.info(f"[{self.name}] | Connecting to {device.address}")
                async with BleakClient(device.address, disconnected_callback=on_dis) as client:
                    logging.info(f"[{self.name}] | Connected to BLE device")
                    self.ble_disconnect.clear()
                    await self.notify_register(client)
                    task = asyncio.create_task(self.start_broadcast(client, str(device.address)))
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            except Exception:
                logging.exception(f"[{self.name}] | BLE connection error")

            await asyncio.sleep(0.7)


    # ------------------------------------------------------------------ #
    #  Encoder inputs (reserved for future use)                           #
    # ------------------------------------------------------------------ #

    async def on_encoder_A_input(self, value: int):
        pass

    async def on_encoder_B_input(self, value: int):
        pass
