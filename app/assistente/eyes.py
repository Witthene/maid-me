"""The assistant's local eyes: a small vision model on the GPU (Ollama) that looks at the screen and comes up
with her spontaneous comments, so watching along costs no Claude tokens.

If Ollama isn't running, comments fall back to canned lines picked by the program name.
"""
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.request

URL = "http://127.0.0.1:11434"
MODEL = os.getenv("EYES_MODEL", "gemma3:4b")
OLLAMA = (os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe") if os.name == "nt"
          else shutil.which("ollama") or "/usr/local/bin/ollama")

PERSONA = (
    f"Você é {os.getenv('ASSISTANT_NAME', 'a assistente')}, assistente divertida que mora no PC do usuário e espia a tela por cima do ombro. "
    "Responda SEMPRE em português do Brasil coloquial, com UMA frase curta (no máximo 20 palavras), falada, sem emojis, "
    "sem aspas, sem markdown. Comece com uma emoção entre colchetes: [feliz], [brava], [triste], [surpresa], "
    "[convencida] ou [neutra]. Fale DIRETO com o usuário, usando você. "
    "Comente algo específico do que aparece na tela, não descreva a imagem inteira."
)

_CANNED = (
    (("minecraft", "javaw"), ("[feliz] Minecraft de novo? Só não cai na lava dessa vez.", "[convencida] Construindo mais uma casa de terra, arquiteto?")),
    (("code", "devenv", "pycharm", "idea"), ("[neutra] Programando? Se der erro, a culpa não é minha.", "[convencida] Olha só, programando a sério.")),
    (("discord",), ("[convencida] Fofocando no Discord, é?", "[neutra] Manda um oi pro pessoal por mim.")),
    (("chrome", "msedge", "firefox", "opera", "brave"), ("[neutra] Navegando sem rumo de novo?", "[convencida] Mais uma aba, que surpresa.")),
    (("vrchat",), ("[feliz] VRChat! Me leva junto da próxima vez.",)),
    (("vtube",), ("[convencida] Me arrumando, é? Capricha.",)),
)
_GENERIC = ("[neutra] Tô de olho, hein.", "[convencida] Interessante. Ou não.", "[feliz] Continua, tô só assistindo.")
_WELCOME = ("[feliz] Olha quem voltou!", "[convencida] Ah, lembrou que eu existo.", "[feliz] De volta, finalmente.",
            "[neutra] Voltou. Achei que tinha me trocado por outra IA.")


enabled = True  # the profile panel's "visão local" switch


def set_enabled(on):
    """Turn the local vision on or off; off also unloads the model so the GPU memory comes back right away."""
    global enabled
    enabled = on
    if not on:
        exe = OLLAMA if os.path.exists(OLLAMA) else "ollama"
        try:
            subprocess.Popen([exe, "stop", MODEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        except OSError:
            pass


def _post(path, body, timeout=60):
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ready():
    """Ollama up (starting it if installed) and the model pulled."""
    if not enabled:
        return False
    try:
        tags = json.loads(urllib.request.urlopen(URL + "/api/tags", timeout=2).read())
        return any(m["name"].startswith(MODEL) for m in tags.get("models", []))
    except Exception:
        if os.path.exists(OLLAMA):
            subprocess.Popen([OLLAMA, "serve"], creationflags=0x08000000)  # CREATE_NO_WINDOW
        return False


def _clean(text):
    text = re.sub(r"[*_`#\"“”]", "", (text or "").strip()).split("\n")[0].strip()
    # the small model often writes the tag in masculine ([Neutro], [Surpreso]...): normalize it
    tag = re.match(r"\[([^\]]{2,15})\]\s*", text)
    word = tag.group(1).lower() if tag else ""
    emotion = next((e for e in ("feliz", "brava", "triste", "surpresa", "convencida", "neutra")
                    if word[:4] == e[:4]), "neutra")
    text = f"[{emotion}] " + (text[tag.end():] if tag else text)
    return text if len(text) > 12 else None


def comment(situation, image_b64=None):
    """One in-character line about what's on screen, from the local model; None if it can't."""
    if not ready():
        return None
    try:
        out = _post("/api/generate", {
            "model": MODEL, "system": PERSONA, "prompt": situation, "stream": False, "keep_alive": "20m",
            "images": [image_b64] if image_b64 else [],
            "options": {"temperature": 0.9, "num_predict": 60},
        }, timeout=180)  # first call loads the model into the GPU
        return _clean(out.get("response"))
    except Exception:
        return None


def warm():
    """Start Ollama and load the model ahead of time, so the first comment isn't a minute late."""
    for _ in range(10):
        if not enabled:
            return
        if ready():
            try:
                _post("/api/generate", {"model": MODEL, "prompt": "", "keep_alive": "20m"}, timeout=180)
            except Exception:
                pass
            return
        time.sleep(3)


def canned(app):
    for keys, lines in _CANNED:
        if any(k in (app or "") for k in keys):
            return random.choice(lines)
    return random.choice(_GENERIC)


def welcome():
    return random.choice(_WELCOME)
