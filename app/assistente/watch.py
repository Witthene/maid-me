"""Watches YouTube along with the user: comments when the video changes and now and then mid-video.

Reading the window title costs nothing; screenshots only go out every WATCH_SECONDS, small JPEGs.
"""
import base64
import ctypes
import io
import re
import threading
import time

from PIL import ImageGrab

ctypes.windll.user32.SetProcessDPIAware()
SUFFIX = re.compile(r"\s*-\s*YouTube.*$")


def foreground_title():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def screenshot_b64(width=960):
    img = ImageGrab.grab()  # primary monitor
    img.thumbnail((width, width))
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=60)
    return base64.b64encode(out.getvalue()).decode()


class Watcher:
    """Puts ("watch", content) events on the queue; the main loop decides if it's a good moment."""

    def __init__(self, events, interval=180, poll=5):
        self.events = events
        self.interval = interval
        self.poll = poll
        self.video = None
        self.last_look = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(self.poll)
            try:
                self._check()
            except Exception as e:
                print(f"[watch] {e}")

    def _check(self):
        title = foreground_title()
        if "YouTube" not in title:
            self.video = None
            return
        video = SUFFIX.sub("", title).strip() or "YouTube"
        if video != self.video:
            self.video = video
            self.last_look = time.time()
            self.events.put(("watch", f"(Olhando a tela) O usuário começou a ver no YouTube: \"{video}\". "
                             "Faça um comentário curtinho e espontâneo sobre isso, uma frase. Não use ferramentas."))
        elif time.time() - self.last_look > self.interval:
            self.last_look = time.time()
            self.events.put(("watch", [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot_b64()}},
                {"type": "text", "text": f"(Olhando a tela) Vocês estão vendo \"{video}\" juntos. "
                 "Comente o que está acontecendo nessa cena numa frase curta, como quem assiste junto. "
                 "Não use ferramentas. Se não tiver nada legal pra dizer, responda só a etiqueta de emoção."},
            ]))
