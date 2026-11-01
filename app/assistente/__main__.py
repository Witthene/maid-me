"""Voice assistant. Mic -> Whisper -> Claude Code or a chat API -> text-to-speech."""
import argparse
import os
import queue
import random
import re
import sys
import threading
import time
from urllib.parse import urlparse

from dotenv import load_dotenv

from assistente.safety import danger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERMISSION_TIMEOUT = 25
IDLE_RESET_MINUTES = 10  # start a fresh conversation after this long idle, so history stops costing tokens
EMOTION_TAG = re.compile(r"\[(feliz|brava|triste|surpresa|convencida|neutra)\]\s*", re.I)
TOOL_LINES = {
    "Bash": "Rodando um comando.",
    "PowerShell": "Rodando um comando.",
    "Read": "Lendo um arquivo.",
    "Glob": "Procurando arquivos.",
    "Grep": "Procurando.",
    "WebSearch": "Pesquisando na internet.",
    "WebFetch": "Abrindo uma página.",
    "Write": "Mexendo num arquivo.",
    "Edit": "Mexendo num arquivo.",
}


def permission_question(req, reason=None):
    name = req.get("tool_name", "")
    inp = req.get("input", {}) or {}
    if reason:
        return f"Opa, essa é séria: vou {reason}. Posso mesmo?"
    if name in ("Bash", "PowerShell"):
        desc = (inp.get("description") or "").strip().rstrip(".")
        return f"Posso {desc[0].lower() + desc[1:]}?" if desc else "Posso rodar um comando?"
    if name in ("Write", "Edit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"Posso mexer no arquivo {os.path.basename(path)}?"
    if name == "WebFetch":
        return f"Posso abrir o site {urlparse(inp.get('url', '')).netloc}?"
    return f"Posso usar a ferramenta {name}?"


class Assistant:
    def __init__(self, args):
        from assistente import stt
        from assistente.brain import ApiSession, ClaudeSession
        from assistente.voice import TextVoice, Voice, make_synth

        self.stt = stt
        self.name = os.getenv("ASSISTANT_NAME", "Assistente")
        self.events = queue.Queue()
        self.text_mode = args.texto
        self.followup = float(os.getenv("FOLLOWUP_SECONDS", "20"))
        try:
            with open(os.path.join(ROOT, "persona.md"), encoding="utf-8") as f:
                self.persona = f.read()
        except OSError:
            raise SystemExit("Falta configurar: abra o Configurar.bat primeiro.")
        provider = os.getenv("BRAIN", "claude-code")
        model = os.getenv("BRAIN_MODEL") or None
        if provider == "claude-code":
            self.make_brain = lambda: ClaudeSession(
                self.persona, os.path.expanduser("~"), self.events,
                model=model, effort=os.getenv("CLAUDE_EFFORT", "low"))
        else:
            self.make_brain = lambda: ApiSession(
                self.persona, os.path.expanduser("~"), self.events, provider,
                os.getenv("BRAIN_API_KEY", ""), model, os.getenv("BRAIN_BASE_URL") or None)

        mute = threading.Event()
        if not self.text_mode:
            from assistente.audio import MicListener, find_device
            print("Carregando o Whisper...")
            self.transcriber = stt.Transcriber(os.getenv("WHISPER_MODEL", "large-v3-turbo"))
            self.mic = MicListener(device=find_device(os.getenv("MIC_DEVICE")))
            mute = self.mic.muted

        synth = None if args.mudo else make_synth(
            os.getenv("VOICE", "edge"), os.getenv("VOICE_API_KEY"), os.getenv("VOICE_ID"),
            os.getenv("VOICE_MODEL") or None)
        if synth:
            self.voice = Voice(synth, mute)
        else:
            print("(sem voz configurada: respondendo so por texto)")
            self.voice = TextVoice()

        if os.getenv("VTUBE_STUDIO", "0") != "0":
            from assistente.body import VTubeBody
            self.body = VTubeBody(self.voice, os.path.join(ROOT, "vts_token.txt"))
            if os.getenv("OVERLAY", "0") != "0":
                from assistente.overlay import Overlay
                self.overlay = Overlay(scale=float(os.getenv("OVERLAY_SCALE", "0.5")))
        else:
            from assistente.body import NoBody
            self.body = NoBody()

        self.idle_reset = float(os.getenv("IDLE_RESET_MINUTES", IDLE_RESET_MINUTES)) * 60
        self.brain = self.make_brain()
        self.turns = 0
        self.last_activity = time.time()
        self.busy = False
        self.listen_until = 0.0
        self.open_window_after_speech = False
        self.pending = None  # permission request waiting for a spoken yes/no
        self.text_this_message = False
        self.watch_turn = False  # unprompted comment: doesn't count as activity, so idle reset still clears history

        if os.getenv("WATCH", "0") != "0":
            from assistente.watch import Watcher
            Watcher(self.events, interval=float(os.getenv("WATCH_SECONDS", "180"))).start()

    # --- input -------------------------------------------------------------
    def _stt_loop(self):
        while True:
            pcm, overlap = self.mic.utterances.get()
            if len(pcm) < 0.5 * 16000:
                continue
            text = self.transcriber.transcribe(pcm)
            if text:
                self.events.put(("heard", (text, overlap)))

    def _stdin_loop(self):
        for line in sys.stdin:
            if line.strip():
                self.events.put(("heard", (line.strip(), False)))
        self.events.put(("quit", None))

    # --- main loop ---------------------------------------------------------
    def run(self):
        if self.text_mode:
            threading.Thread(target=self._stdin_loop, daemon=True).start()
        else:
            self.mic.start()
            threading.Thread(target=self._stt_loop, daemon=True).start()
        self.voice.say(f"{self.name} online.")
        print(f"Pronto. Diga \"Ei, {self.name}\" e o que voce quer. Ctrl+C para sair.\n")
        quitting = False
        while not (quitting and not self.busy and not self.pending):
            try:
                kind, data = self.events.get(timeout=0.3)
            except queue.Empty:
                kind, data = None, None
            if kind == "quit":
                quitting = True
            if kind in ("claude", "claude_exit"):
                session, data = data
                if session is not self.brain:
                    continue
            if kind == "heard":
                self.on_heard(*data)
            elif kind == "watch":
                self.on_watch(data)
            elif kind == "claude":
                self.on_claude(data)
            elif kind == "claude_exit":
                print(f"[cerebro] processo terminou (codigo {data}); reiniciando")
                self.busy, self.pending = False, None
                self.voice.say("Meu cérebro reiniciou. Esqueci o que a gente estava falando.")
                self.brain = self.make_brain()
            self.tick()
        self.brain.close()

    def tick(self):
        if self.open_window_after_speech and self.voice.idle.is_set():
            self.open_window_after_speech = False
            self.listen_until = time.time() + self.followup
        if self.pending and self.voice.idle.is_set() and time.time() > self.pending["deadline"]:
            print("   (sem resposta: negado)")
            self.resolve_permission(False)
        if (self.turns and not self.busy and not self.pending
                and time.time() - self.last_activity > self.idle_reset):
            print("   (parada ha um tempo: comecando conversa nova para economizar tokens)")
            old, self.brain = self.brain, self.make_brain()
            old.close()
            self.turns = 0

    def on_heard(self, text, overlap=False):
        if overlap:
            # heard while she was talking: probably her own voice, only a stop command counts
            if self.stt.heard_stop(text):
                print(f"Voce: {text}\n   (interrompendo)")
                self.voice.stop()
                if self.busy:
                    self.brain.interrupt()
            return
        print(f"Voce: {text}")
        if self.pending:
            answer = self.stt.yes_no(text)
            if answer is None:
                self.pending["tries"] += 1
                if self.pending["tries"] >= 2:
                    self.resolve_permission(False)
                else:
                    self.voice.say("Sim ou não?")
                    self.pending["deadline"] = time.time() + PERMISSION_TIMEOUT
                return
            self.resolve_permission(answer)
            return

        woke = self.text_mode or self.stt.has_wake(text)
        if self.busy:
            if woke and self.stt.is_stop(text):
                print("   (interrompendo)")
                self.voice.stop()
                self.brain.interrupt()
            elif woke:
                # Claude Code takes extra user messages mid-turn, so pass it along instead of dropping it
                print("   (ocupada: mensagem entregue mesmo assim)")
                self.voice.say(random.choice(["Anotado.", "Já vejo isso.", "Ouvi."]))
                self.brain.send(text)
            else:
                print("   (ocupado, sem o nome, ignorando)")
            return
        if not woke and time.time() > self.listen_until:
            print("   (sem o nome, ignorando)")
            return
        if not self.text_mode and self.stt.only_wake(text):
            self.voice.say(random.choice(["Fala.", "Diz aí.", "Tô ouvindo."]))
            self.open_window_after_speech = True
            return
        self.listen_until = 0
        self.busy = True
        self.body.thinking = True
        self.last_activity = time.time()
        self.brain.send(text)

    def on_watch(self, content):
        # only chime in when nobody is talking
        if (self.busy or self.pending or not self.voice.idle.is_set()
                or time.time() < self.listen_until or time.time() - self.last_activity < 20):
            return
        print("   (comentando o que esta na tela)")
        self.busy = self.watch_turn = True
        self.brain.send(content)

    # --- claude events ------------------------------------------------------
    def on_claude(self, msg):
        kind = msg.get("type")
        if kind == "assistant":
            texts = [c["text"] for c in msg["message"]["content"] if c.get("type") == "text"]
            tools = [c for c in msg["message"]["content"] if c.get("type") == "tool_use"]
            for t in texts:
                emotions = EMOTION_TAG.findall(t)
                t = EMOTION_TAG.sub("", t).strip()
                if emotions:
                    self.body.set_emotion(emotions[-1].lower())
                if t:
                    print(f"{self.name}: {t}")
                    self.body.thinking = False
                    self.voice.say(t)
                    self.text_this_message = True
            if tools:
                self.body.thinking = True
            for tool in tools:
                print(f"   [{tool['name']}] {str(tool.get('input'))[:160]}")
                if not self.text_this_message and tool["name"] in TOOL_LINES:
                    self.voice.say(TOOL_LINES[tool["name"]])
                    self.text_this_message = True
            if tools:
                self.text_this_message = False
        elif kind == "control_request":
            req = msg.get("request", {})
            if req.get("subtype") == "can_use_tool":
                reason = danger(req.get("tool_name", ""), req.get("input", {}) or {})
                if not reason:
                    self.brain.answer_permission(msg, True)
                    return
                question = permission_question(req, reason)
                self.body.thinking = False
                self.body.set_emotion("surpresa")
                print(f"   [permissao] {req.get('tool_name')}: {str(req.get('input'))[:300]}")
                print(f"{self.name}: {question}")
                self.voice.say(question)
                self.pending = {"msg": msg, "tries": 0, "deadline": time.time() + PERMISSION_TIMEOUT}
            else:
                self.brain.reject_request(msg)
        elif kind == "result":
            self.busy = False
            self.body.thinking = False
            self.turns += 1
            if not self.watch_turn:
                self.last_activity = time.time()
            self.watch_turn = False
            self.text_this_message = False
            if msg.get("is_error"):
                print(f"[cerebro] erro: {msg.get('subtype')} {msg.get('result', '')}")
            print("   (pronto)")
            self.open_window_after_speech = True

    def resolve_permission(self, allow):
        print("   (autorizado)" if allow else "   (negado)")
        self.brain.answer_permission(self.pending["msg"], allow)
        self.pending = None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
    parser = argparse.ArgumentParser(prog="assistente")
    parser.add_argument("--mics", action="store_true", help="lista os microfones e sai")
    parser.add_argument("--texto", action="store_true", help="digitar em vez de falar")
    parser.add_argument("--mudo", action="store_true", help="responder so em texto")
    args = parser.parse_args()
    if args.mics:
        from assistente.audio import list_devices
        list_devices()
        return
    try:
        Assistant(args).run()
    except KeyboardInterrupt:
        print("\nTchau.")


if __name__ == "__main__":
    main()
