"""Speech-to-text with faster-whisper (GPU if there is one), plus wake-word and yes/no parsing."""
import glob
import os
import re
import sys
import unicodedata


def _add_cuda_dlls():
    # cuBLAS/cuDNN come from pip wheels; ctranslate2 needs their bin folders on the DLL path
    root = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    for d in glob.glob(os.path.join(root, "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


_add_cuda_dlls()
import ctranslate2  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

NAME = os.getenv("ASSISTANT_NAME", "Assistente")

# Things Whisper likes to "hear" in noise
JUNK = {
    "legendas pela comunidade amara org",
    "obrigado por assistir",
    "inscreva se no canal",
    "e ai",
}


def normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _names_pattern():
    # the name plus the other spellings Whisper comes up with, set in the configurator
    names = [NAME] + os.getenv("WAKE_ALIASES", "").split(",")
    alts = sorted({normalize(n) for n in names if normalize(n)}, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(a).replace(r"\ ", r"\s+") for a in alts) + ")"


# The name can come after up to three words ("ô, então, Fulana...")
NAMES = _names_pattern()
WAKE = re.compile(r"^(?:[a-z0-9]+\s+){0,3}?" + NAMES + r"\b")
YES = {"sim", "pode", "claro", "manda", "vai", "beleza", "ok", "okay", "autorizo", "faz", "bora",
       "isso", "positivo", "uhum", "aham", "libera", "confirmo", "fechou", "demorou", "certo"}
NO = {"nao", "negativo", "cancela", "cancelar", "para", "pare", "espera", "nem", "nunca", "deixa"}
STOP = {"para", "pare", "parar", "paro", "cancela", "cancelar", "chega", "silencio", "espera", "pera"}


def has_wake(text):
    return WAKE.match(normalize(text)) is not None


def only_wake(text):
    n = normalize(text)
    m = WAKE.match(n)
    return m is not None and not n[m.end():].strip()


def yes_no(text):
    words = set(normalize(text).split())
    if words & NO:
        return False
    if words & YES:
        return True
    return None


STOP_ANYWHERE = re.compile(r"\b" + NAMES + r"\s+(?:"
                           + "|".join(STOP) + r")\b")


# specific enough to count anywhere, even without the name
STOP_PHRASES = re.compile(r"\b(?:cala(?:r)? (?:a )?boca|cal[ae] (?:a )?boca|fica quieta|fique quieta|fica quieto|para de falar"
                          r"|pare de falar|chega de falar|para com isso|silencio|basta|shh+|xiu|psiu|quieta|quieto)\b")


def heard_stop(text):
    """'<nome>, para' anywhere in the text, or 'cala a boca' with the name, for audio mixed with its own voice."""
    n = normalize(text)
    if STOP_ANYWHERE.search(n) is not None:
        return True
    # without the name it's too easy to trigger from its own voice or a Whisper hallucination
    return re.search(r"\b" + NAMES + r"\b", n) is not None and STOP_PHRASES.search(n) is not None


def is_stop(text):
    n = normalize(text)
    m = WAKE.match(n)
    rest = n[m.end():].split() if m else n.split()
    return (bool(rest) and len(rest) <= 3 and rest[0] in STOP) or (len(rest) <= 5 and STOP_PHRASES.search(" ".join(rest)) is not None)


class Transcriber:
    def __init__(self, model="large-v3-turbo"):
        if ctranslate2.get_cuda_device_count() > 0:
            self.model = WhisperModel(model, device="cuda", compute_type="float16")
        else:
            # no NVIDIA card: a smaller model on the CPU keeps the delay bearable
            cpu_model = os.getenv("WHISPER_CPU_MODEL", "small")
            print(f"(sem placa NVIDIA: usando o Whisper '{cpu_model}' no processador)")
            self.model = WhisperModel(cpu_model, device="cpu", compute_type="int8")

    def transcribe(self, pcm):
        # the prompt nudges Whisper toward the name, the stop phrases and casual Brazilian speech
        segments, _ = self.model.transcribe(pcm, language="pt", beam_size=5, hotwords=NAME,
                                            initial_prompt=f"Ei, {NAME}, abre o Chrome.",
                                            condition_on_previous_text=False)
        parts = [s.text.strip() for s in segments
                 if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(parts).strip()
        if not text or normalize(text) in JUNK:
            return None
        return text
