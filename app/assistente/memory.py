"""Remembers recent conversations across restarts without spending tokens to summarize them.

Every line said is kept locally (trimmed); a fresh brain gets only the last few, short, in its persona.
"""
import json
import os
import threading
import time

KEEP = 200  # lines kept on disk
RECAP_LINES = 16  # lines handed to a new conversation, enough for context, cheap on tokens
LINE_CHARS = 160


class Memory:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self.lines = json.load(f)
        except (OSError, ValueError):
            self.lines = []

    def add(self, who, text):
        text = " ".join(str(text).split())
        if not text:
            return
        if len(text) > LINE_CHARS:
            text = text[:LINE_CHARS].rstrip() + "..."
        with self.lock:
            self.lines.append({"t": time.time(), "who": who, "text": text})
            self.lines = self.lines[-KEEP:]
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.lines, f, ensure_ascii=False)
            except OSError:
                pass

    def recap(self):
        with self.lock:
            recent = self.lines[-RECAP_LINES:]
        if not recent:
            return ""
        out, last_day = [], None
        for line in recent:
            day = time.strftime("%d/%m", time.localtime(line["t"]))
            if day != last_day:
                out.append(f"[{day}]")
                last_day = day
            out.append(f"{'Usuário' if line['who'] == 'ele' else 'Você'} ({time.strftime('%H:%M', time.localtime(line['t']))}): {line['text']}")
        return ("\n\n# Memória das últimas conversas\n"
                "Trechos do que vocês falaram antes (agora são " + time.strftime("%d/%m %H:%M") + "). "
                "Use pra dar continuidade e fazer referências naturais quando fizer sentido; "
                "não recite isso nem diga que está lendo um registro.\n" + "\n".join(out))


class NoMemory:
    """Stand-in when the user turned memory off: nothing is kept."""

    def add(self, who, text):
        pass

    def recap(self):
        return ""
