"""Microphone capture split into utterances.

The mic is opened through WASAPI at its native rate, resampled to 16 kHz with soxr, and
cut into utterances by Silero VAD (the speech detector that ships with faster-whisper).
Each utterance is cleaned up (low rumble removed, volume evened out) before Whisper.
"""
import collections
import queue
import threading

import numpy as np
import sounddevice as sd
import soxr
from faster_whisper.vad import get_vad_model
from scipy.signal import butter, sosfilt

RATE = 16000
CHUNK = 512  # Silero works on 32 ms chunks at 16 kHz
CONTEXT = 64
START_PROB, END_PROB = 0.5, 0.35
START_CHUNKS = 3  # ~100 ms of speech before an utterance starts
PREROLL_CHUNKS = 16  # ~0.5 s kept from before the start, so the first syllable isn't cut
MIN_SPEECH_CHUNKS = 8  # ~250 ms of actual speech, shorter blips are dropped
TARGET_RMS, MAX_GAIN = 0.08, 10.0
HIGHPASS = butter(4, 80, btype="highpass", fs=RATE, output="sos")


class SpeechDetector:
    """Streaming Silero VAD: one probability per 512-sample chunk, keeping its state between calls."""

    def __init__(self):
        self._session = get_vad_model().session
        self.reset()

    def reset(self):
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, chunk):
        x = np.concatenate([self._context, chunk])[None, :]
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = chunk[-CONTEXT:]
        return float(out.ravel()[0])


def clean(pcm):
    """Remove rumble below 80 Hz and bring the voice to a steady level."""
    pcm = sosfilt(HIGHPASS, pcm).astype(np.float32)
    rms = float(np.sqrt(np.mean(pcm ** 2))) or 1e-6
    gain = min(MAX_GAIN, TARGET_RMS / rms)
    return np.clip(pcm * gain, -1, 1).astype(np.float32)


class MicListener:
    def __init__(self, device=None, silence_ms=1000, max_ms=30000):
        self.utterances = queue.Queue()
        self.muted = threading.Event()  # set while the assistant is talking, so she doesn't hear herself
        self._device = device
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self._rate = int(info["default_samplerate"])
        self._channels = min(2, info["max_input_channels"])
        self._resampler = soxr.ResampleStream(self._rate, RATE, 1, dtype="float32", quality="HQ")
        self._vad = SpeechDetector()
        self._silence_chunks = silence_ms * RATE // 1000 // CHUNK
        # "<nome>, para" over her own voice is short: cut it sooner so the stop lands faster
        self._overlap_silence_chunks = 400 * RATE // 1000 // CHUNK
        self._max_chunks = max_ms * RATE // 1000 // CHUNK
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll = collections.deque(maxlen=PREROLL_CHUNKS)
        self._chunks = queue.Queue()
        self._reset()

    def start(self):
        self._stream = sd.InputStream(samplerate=self._rate, channels=self._channels, dtype="float32",
                                      device=self._device, callback=self._on_audio)
        self._stream.start()
        threading.Thread(target=self._detect_loop, daemon=True).start()

    def _reset(self):
        self._voiced = []
        self._speech_run = 0
        self._silent = 0
        self._triggered = False
        self._overlap = False  # did the assistant speak during this utterance (so it may contain her own voice)

    def _on_audio(self, indata, frames, time_info, status):
        # keep the audio callback light: just hand the raw block to the detector thread
        self._chunks.put(indata.mean(axis=1).copy())

    def _detect_loop(self):
        while True:
            block = self._chunks.get()
            # keep listening while the assistant talks so "<nome>, para" can cut her off; those utterances get flagged
            self._pending = np.concatenate([self._pending, self._resampler.resample_chunk(block)])
            while len(self._pending) >= CHUNK:
                chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
                self._feed(chunk)

    def _feed(self, chunk):
        prob = self._vad(chunk)
        if self.muted.is_set():
            self._overlap = True
        if not self._triggered:
            if not self.muted.is_set() and self._speech_run == 0:
                self._overlap = False
            self._preroll.append(chunk)
            self._speech_run = self._speech_run + 1 if prob >= START_PROB else 0
            if self._speech_run >= START_CHUNKS:
                self._triggered = True
                self._voiced = list(self._preroll)
                self._preroll_len = len(self._voiced) - START_CHUNKS
                self._preroll.clear()
            return
        self._voiced.append(chunk)
        self._silent = 0 if prob >= END_PROB else self._silent + 1
        limit = self._overlap_silence_chunks if self._overlap else self._silence_chunks
        if self._silent >= limit or len(self._voiced) >= self._max_chunks:
            speech = self._voiced[:len(self._voiced) - self._silent + 3]  # drop most of the trailing silence
            # clicks and coughs barely pass the start check; real words are longer than ~250 ms
            if len(speech) - self._preroll_len >= MIN_SPEECH_CHUNKS:
                self.utterances.put((clean(np.concatenate(speech)), self._overlap))
            self._reset()


def _wasapi(name):
    """Index of the WASAPI input device whose name contains `name`, or None."""
    apis = sd.query_hostapis()
    for i, dev in enumerate(sd.query_devices()):
        if (dev["max_input_channels"] > 0 and "WASAPI" in apis[dev["hostapi"]]["name"]
                and name.lower() in dev["name"].lower()):
            return i
    return None


def find_device(name):
    """Input device to use, preferring the WASAPI version (native rate, no Windows resampling)."""
    if not name:
        default = sd.query_devices(sd.default.device[0])["name"]
        return _wasapi(default.split("(")[-1].rstrip(")")) if "(" in default else _wasapi(default)
    found = _wasapi(name)
    if found is not None:
        return found
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return i
    raise SystemExit(f"Microfone '{name}' nao encontrado. Rode com --mics para ver a lista.")


def list_devices():
    apis = sd.query_hostapis()
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and "WASAPI" in apis[dev["hostapi"]]["name"]:
            print(f"  {i:3d}  {dev['name']}")
