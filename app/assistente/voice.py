"""The assistant's voice: text-to-speech fetched one sentence ahead of playback.

Providers: Microsoft Edge voices (free, no key), Fish Audio and ElevenLabs.
"""
import asyncio
import io
import queue
import re
import threading

import av
import numpy as np
import requests
import sounddevice as sd

RATE = 44100
UNMUTE_DELAY = 0.3  # seconds of quiet after it stops, so the mic doesn't catch the tail
EDGE_VOICES = ["pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural", "pt-BR-AntonioNeural",
               "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"]


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "o link", text)
    text = re.sub(r"[`*_#>|]", "", text)
    return " ".join(text.split())


def split_sentences(text, min_len=25):
    pieces = [p for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    merged = []
    for p in pieces:
        if merged and len(merged[-1]) < min_len:
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged


def decode(data):
    """Any compressed audio (mp3...) -> mono int16 at RATE."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().ravel() for f in resampler.resample(frame)]
    chunks += [f.to_ndarray().ravel() for f in resampler.resample(None)]
    return np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, dtype=np.int16)


# --- providers: text -> int16 samples at RATE ---------------------------------
def edge_synth(voice):
    import edge_tts

    async def fetch(text):
        audio = bytearray()
        async for chunk in edge_tts.Communicate(text, voice or EDGE_VOICES[0]).stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return bytes(audio)

    return lambda text: decode(asyncio.run(fetch(text)))


def fish_synth(api_key, voice_id, model=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if model:
        headers["model"] = model

    def synth(text):
        body = {"text": text, "reference_id": voice_id, "format": "pcm",
                "sample_rate": RATE, "latency": "balanced", "normalize": True}
        r = requests.post("https://api.fish.audio/v1/tts", json=body, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return np.frombuffer(r.content[: len(r.content) // 2 * 2], dtype=np.int16)

    return synth


def elevenlabs_synth(api_key, voice_id, model=None):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"

    def synth(text):
        r = requests.post(url, headers={"xi-api-key": api_key},
                          json={"text": text, "model_id": model or "eleven_multilingual_v2"}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return decode(r.content)

    return synth


def make_synth(provider, api_key=None, voice_id=None, model=None):
    """Synth function for the provider chosen in the configurator, or None for text only."""
    if provider == "edge":
        return edge_synth(voice_id)
    if provider == "fish" and api_key and voice_id:
        return fish_synth(api_key, voice_id, model)
    if provider == "elevenlabs" and api_key and voice_id:
        return elevenlabs_synth(api_key, voice_id, model)
    return None


def play(pcm):
    sd.play(pcm, RATE)
    sd.wait()


class Voice:
    def __init__(self, synth, mute_event):
        self._synth = synth
        self._mute = mute_event
        self._texts = queue.Queue()
        self._audio = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self._generation = 0
        self.level = 0.0  # loudness of what is playing right now, for lip sync
        self.idle = threading.Event()
        self.idle.set()
        threading.Thread(target=self._fetch_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def say(self, text):
        text = clean_for_speech(text)
        for sentence in split_sentences(text):
            with self._lock:
                self._pending += 1
                self.idle.clear()
                self._mute.set()
            self._texts.put((self._generation, sentence))

    def stop(self):
        self._generation += 1

    def _fetch_loop(self):
        while True:
            gen, text = self._texts.get()
            if gen != self._generation:
                self._finished()
                continue
            try:
                pcm = self._synth(text)
            except Exception as e:
                print(f"[voz] erro na voz: {e}")
                pcm = None
            self._audio.put((gen, pcm))

    def _play_loop(self):
        while True:
            gen, pcm = self._audio.get()
            if pcm is not None and len(pcm) and gen == self._generation:
                self._play(gen, pcm)
            self._finished()

    def _play(self, gen, pcm):
        pos = 0
        done = threading.Event()

        def callback(outdata, frames, time_info, status):
            nonlocal pos
            if gen != self._generation:
                raise sd.CallbackStop
            chunk = pcm[pos:pos + frames]
            outdata[:len(chunk), 0] = chunk
            outdata[len(chunk):, 0] = 0
            self.level = float(np.sqrt(np.mean((chunk / 32768.0) ** 2))) if len(chunk) else 0.0
            pos += frames
            if pos >= len(pcm):
                raise sd.CallbackStop

        with sd.OutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=1024,
                             callback=callback, finished_callback=done.set):
            done.wait()
        self.level = 0.0

    def _finished(self):
        with self._lock:
            self._pending -= 1
            if self._pending == 0:
                self.idle.set()
                threading.Timer(UNMUTE_DELAY, self._unmute).start()

    def _unmute(self):
        with self._lock:
            if self._pending == 0:
                self._mute.clear()


class TextVoice:
    """Stand-in when there is no voice configured: prints instead of speaking."""

    def __init__(self):
        self.level = 0.0
        self.idle = threading.Event()
        self.idle.set()

    def say(self, text):
        print(f"   (voz) {clean_for_speech(text)}")

    def stop(self):
        pass
