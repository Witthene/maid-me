"""Watches YouTube along with the user: comments when the video changes and now and then mid-video.

Comments come from the local vision model (assistente/eyes.py), so watching costs no Claude tokens.
"""
import base64
import io
import random
import re
import threading
import time

from PIL import ImageGrab

from assistente import eyes, hub, plat

plat.dpi_aware()
SUFFIX = re.compile(r"\s*-\s*YouTube.*$")
APP_SETTLE = 45  # seconds on a program before it counts as "using it"
APP_GAP = (3 * 60, 8 * 60)  # random pause between program comments
APP_REPEAT = 20 * 60  # same program, at most once every 20 minutes
APP_CHANCE = 0.7  # and even then she sometimes keeps quiet
AWAY = 10 * 60  # no mouse/keyboard this long counts as "he left"


idle_seconds = plat.idle_seconds


def look(text, width=640):
    """Message content for Claude with a fresh look at his screen: small and gray to spend few tokens."""
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot_b64(width, gray=True)}},
            {"type": "text", "text": text}]


def foreground_title():
    w = plat.foreground()
    return w.title if w else ""


def foreground_app():
    """Program name of the focused window, like "chrome" or "javaw"."""
    w = plat.foreground()
    return w.app if w else ""


# her own window, the desktop and system popups aren't worth a comment
IGNORED_APPS = {"", "python", "pythonw", "python3", "explorer", "searchhost", "startmenuexperiencehost",
                "shellexperiencehost", "lockapp", "taskmgr", "applicationframehost", "textinputhost",
                "gnome-shell", "plasmashell", "xfdesktop", "nautilus", "dolphin"}


def screenshot_b64(width=960, gray=False):
    """Small JPEG of the monitor the user is working on (not always the primary one)."""
    img = ImageGrab.grab(all_screens=True) if plat.WINDOWS else ImageGrab.grab()
    box, (vx, vy) = plat.focus_monitor()
    if box:
        # all_screens images start at the virtual desktop's top-left, which can be negative
        img = img.crop((box[0] - vx, box[1] - vy, box[2] - vx, box[3] - vy))
    img.thumbnail((width, width))
    if gray:
        img = img.convert("L")
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
        self.app = None  # focused app and since when, so quick alt-tabs don't count
        self.app_since = 0.0
        self.app_seen = {}  # app -> last time she commented on it
        self.next_app_comment = time.time() + random.uniform(*APP_GAP)
        self.away_since = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        eyes.warm()
        while True:
            time.sleep(self.poll)
            try:
                self._check()
            except Exception as e:
                hub.error("tela", str(e))

    def _check(self):
        idle = idle_seconds()
        if idle > AWAY:
            self.away_since = self.away_since or time.time() - idle
            return  # nobody to talk to
        if self.away_since:
            self.away_since = 0.0
            self.app_since = time.time()
            self.events.put(("say", eyes.welcome()))  # canned: free and instant
            return
        title = foreground_title()
        self._check_app(title)
        if "YouTube" not in title:
            self.video = None
            return
        video = SUFFIX.sub("", title).strip() or "YouTube"
        if video != self.video:
            self.video = video
            self.last_look = time.time()
            self._local(f"O usuário começou a ver no YouTube o vídeo \"{video}\". Comente sobre o vídeo.", None,
                        "[feliz] Opa, vídeo novo. Vou assistir junto, hein.")
        elif time.time() - self.last_look > self.interval:
            self.last_look = time.time()
            self._local(f"Vocês estão vendo juntos o vídeo \"{video}\". Comente o que está acontecendo nessa cena, "
                        "como quem assiste junto.", screenshot_b64(768), None)

    def _check_app(self, title):
        """Comment when he settles into another program, looking at what's actually on screen; rare and random."""
        app = foreground_app()
        now = time.time()
        if app != self.app:
            self.app, self.app_since = app, now
            return
        if (app in IGNORED_APPS or "YouTube" in title or now - self.app_since < APP_SETTLE
                or now < self.next_app_comment or now - self.app_seen.get(app, 0) < APP_REPEAT):
            return
        self.app_seen[app] = now
        self.next_app_comment = now + random.uniform(*APP_GAP)
        if random.random() > APP_CHANCE:
            return
        self._local(f"O usuário está no programa \"{app}\", janela \"{title[:80]}\". A imagem é a tela do usuário agora. "
                    "Reaja ao que o usuário está fazendo de fato (o jogo, o código, o vídeo, a conversa).",
                    screenshot_b64(768), eyes.canned(app))

    def _local(self, situation, image, fallback):
        """Spontaneous comment made by the local vision model (no Claude tokens); canned line if it's off."""
        if image:
            hub.look()
        line = eyes.comment(situation, image) or fallback
        if line:
            self.events.put(("say", line))
