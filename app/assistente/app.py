"""Yui's app window, in the style of WhatsApp on iPhone with Liquid Glass.

Frameless, translucent window with the desktop blurred behind it (Windows acrylic), glass header
and message bar, chat bubbles, live status ("digitando...", "falando..."), permission cards with
buttons, a text box and mic/stop controls.

The window owns the main thread; Yui runs in a background thread and talks to the window through
hub.sink, which is turned into a Qt signal so everything is drawn on the GUI thread.
"""
import ctypes
import json
import os
import random
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, QBuffer, QByteArray, QIODevice, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QMovie, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect,QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QScrollArea, QSizeGrip, QSlider, QVBoxLayout, QWidget, QWidgetAction)

from assistente import hub, plat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SIGILS = os.path.join(ASSETS, "sigils")  # netrunner marks (SVG), picked from the profile photo menu
SETTINGS = os.path.join(ROOT, "app_settings.json")  # wallpaper and its brightness
CLEAR = QColor(0, 0, 0, 0)
RADIUS = 12  # window corners, close to Windows 11's own so the blur never peeks out


def rgba(r, g, b, a=255):
    return QColor(r, g, b, a)


# --- palette: dark glass with Yui's pink and violet ---------------------------------
TEXT, SUBTEXT = rgba(255, 250, 253), rgba(240, 228, 250, 165)
WINDOW_TINT, ACRYLIC_TINT, WINDOW_BORDER = rgba(22, 14, 34, 165), (18, 10, 30, 110), rgba(255, 210, 240, 70)
BLOBS = [rgba(255, 95, 168, 70), rgba(143, 107, 255, 75), rgba(125, 211, 252, 35)]
GLASS_TOP, GLASS_BOTTOM = rgba(255, 255, 255, 34), rgba(255, 255, 255, 12)
RIM_TOP, RIM_BOTTOM, SHEEN = rgba(255, 255, 255, 110), rgba(255, 255, 255, 18), rgba(255, 255, 255, 22)
SMOKE = rgba(24, 14, 36, 150)  # smoky glass base
DEFAULT_ACCENT = rgba(255, 95, 168)
ACCENT = DEFAULT_ACCENT  # follows the wallpaper: see accent_from() and ChatWindow.apply_accent()


def tint(color, alpha, lighter=100):
    c = QColor(color).lighter(lighter)
    c.setAlpha(alpha)
    return c


def accent_from(pixmap):
    """The wallpaper's most striking color, tuned to work as an accent on dark glass."""
    img = pixmap.toImage().scaled(64, 64, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    buckets = {}
    for y in range(img.height()):
        for x in range(img.width()):
            h, s, v, _ = img.pixelColor(x, y).getHsvF()
            if h < 0 or s < 0.25 or v < 0.25:
                continue  # greys, whites and shadows say nothing about the mood
            key = int(h * 24)
            weight = s * s * v
            total, hue_sum = buckets.get(key, (0.0, 0.0))
            buckets[key] = (total + weight, hue_sum + h * weight)
    if not buckets:
        return DEFAULT_ACCENT
    total, hue_sum = max(buckets.values())
    return QColor.fromHsvF(hue_sum / total, 0.62, 0.95)
PINK, PURPLE, CYAN = rgba(255, 95, 168), rgba(176, 150, 255), rgba(125, 211, 252)
OK, WARN, DANGER, TICKS = rgba(126, 224, 166), rgba(250, 204, 21), rgba(255, 92, 120), rgba(130, 205, 255)
EMOTIONS = {"feliz": ("♪ feliz", OK), "brava": ("× brava", DANGER), "triste": ("… triste", CYAN),
            "surpresa": ("! surpresa", WARN), "convencida": ("★ convencida", PINK)}
I_MIC, I_MIC_OFF, I_STOP, I_GEAR, I_SEND, I_CHECK, I_CLOSE, I_MIN, I_SHIELD = (
    "", "", "", "", "", "", "", "", "")


def qss(c):
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def load_fonts():
    folder = os.path.join(ASSETS, "fonts")
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.endswith(".ttf"):
                QFontDatabase.addApplicationFont(os.path.join(folder, f))
    return "Poppins" if "Poppins" in QFontDatabase.families() else "Segoe UI"


ICON_FONT = "Segoe Fluent Icons" if plat.WINDOWS else "DejaVu Sans"
# Segoe Fluent Icons only exists on Windows: elsewhere each glyph becomes a plain Unicode symbol
LINUX_ICONS = {"\ue713": "⚙", "\ue717": "☎", "\ue71a": "■", "\ue720": "🎙", "\ue724": "➤", "\ue73e": "✓",
               "\ue74d": "✕", "\ue771": "✎", "\ue778": "☎", "\ue7f4": "▭", "\ue8bb": "✕", "\ue8bd": "✉",
               "\ue91b": "▣", "\ue921": "—", "\uea18": "⛨", "\uec54": "🔇"}


def icon(glyph):
    return glyph if plat.WINDOWS else "".join(LINUX_ICONS.get(c, c) for c in glyph)
I_PHONE, I_HANG_UP = "", ""


def font(size, weight=QFont.Normal):
    f = QFont(FONT_FAMILY)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def paint_glass(p, rect, radius, top=GLASS_TOP, bottom=GLASS_BOTTOM, rim_top=RIM_TOP, rim_bottom=RIM_BOTTOM,
                sheen=SHEEN, backing=None, path=None):
    """Frosted fill + vertical sheen + light-catching rim (same look as the Klicker).

    backing: a smoky base under the glass, so text stays readable over busy wallpapers.
    path: a custom shape instead of the rounded rectangle (bubbles with a tail corner)."""
    if path is None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
    if backing is not None:
        p.fillPath(path, backing)
    fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    fill.setColorAt(0, top)
    fill.setColorAt(1, bottom)
    p.fillPath(path, QBrush(fill))
    if sheen is not None:
        gloss = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.55))
        gloss.setColorAt(0, sheen)
        gloss.setColorAt(1, CLEAR)
        p.fillPath(path, QBrush(gloss))
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0, rim_top)
    rim.setColorAt(1, rim_bottom)
    p.setPen(QPen(QBrush(rim), 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


# --- Windows chrome: acrylic blur and rounded corners ---------------------------------------
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint32), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attrib", ctypes.c_int), ("pvData", ctypes.c_void_p), ("cbData", ctypes.c_size_t)]


def set_acrylic(hwnd, tint):
    if not plat.WINDOWS:
        return False  # the Linux compositor decides about blur: the window just stays translucent
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = (ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA))
        r, g, b, a = tint
        policy = ACCENT_POLICY(4, 2, (a << 24) | (b << 16) | (g << 8) | r, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
                                           ctypes.sizeof(policy))
        fn(ctypes.c_void_p(hwnd), ctypes.byref(data))
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))
        return True
    except Exception:
        return False


# --- widgets ------------------------------------------------------------------------------------
class GlyphButton(QAbstractButton):
    """Round glass button with an icon from Windows' icon font."""

    def __init__(self, glyph, size=36, tip="", fill=None, flat=False):
        super().__init__()
        self.glyph, self.fill, self.flat = glyph, fill, flat
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.flat and self.fill is None:
            if self.underMouse():  # buttons inside a capsule only light up on hover
                path = QPainterPath()
                path.addEllipse(r)
                p.fillPath(path, rgba(255, 255, 255, 30))
        elif self.fill is not None:
            top = QColor(self.fill).lighter(118 if self.underMouse() else 108)
            paint_glass(p, r, r.height() / 2, top, self.fill, rgba(255, 255, 255, 120), rgba(255, 255, 255, 20))
        else:
            boost = 18 if self.underMouse() else 0  # same smoky glass as the capsules and the message field
            paint_glass(p, r, r.height() / 2, rgba(255, 255, 255, 22 + boost), rgba(255, 255, 255, 8 + boost),
                        rgba(255, 255, 255, 60), rgba(255, 255, 255, 10), backing=SMOKE)
        f = QFont(ICON_FONT)
        f.setPixelSize(int(self.height() * 0.42))
        p.setFont(f)
        p.setPen(TEXT)
        p.drawText(r, Qt.AlignCenter, icon(self.glyph))


class GlassBar(QFrame):
    def __init__(self, radius=0):
        super().__init__()
        self.radius = radius

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius,
                    rgba(255, 255, 255, 22), rgba(255, 255, 255, 8), rgba(255, 255, 255, 60), rgba(255, 255, 255, 10),
                    backing=SMOKE)


class Bubble(QFrame):
    """A chat bubble: glass for Yui, pink for the user, time (and read ticks) tucked in the corner."""

    def __init__(self, text, mine, emotion=None):
        super().__init__()
        self.mine = mine
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 6, 10, 5)
        layout.setSpacing(0)
        if not mine and emotion in EMOTIONS:
            label, color = EMOTIONS[emotion]
            tag = QLabel(label)
            tag.setFont(font(10, QFont.DemiBold))
            tag.setStyleSheet(f"color: {qss(color)}; background: transparent;")
            layout.addWidget(tag)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setFont(font(13))
        body.setStyleSheet(f"color: {qss(TEXT)}; background: transparent;")
        self.body = body
        # word-wrapped labels shrink to their minimum, so start them as wide as the text needs
        text_width = self.text_width = body.fontMetrics().horizontalAdvance(text) + 6
        self.fit(270)
        stamp = QLabel(time.strftime("%H:%M") + (" ✓✓" if mine else ""))
        stamp.setFont(font(9))
        stamp.setStyleSheet(f"color: {qss(rgba(255, 255, 255, 190) if mine else SUBTEXT)}; background: transparent;")
        if text_width < 210:
            # short message: time sits on the same line, at the end, like WhatsApp
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(body)
            line.addWidget(stamp, 0, Qt.AlignBottom)
            layout.addLayout(line)
        else:
            layout.addWidget(body)
            stamp.setAlignment(Qt.AlignRight)
            layout.addWidget(stamp)

    def fit(self, width):
        """Follow the window width: bubbles grow and shrink with it."""
        self.body.setMaximumWidth(width)
        self.body.setMinimumWidth(min(width, self.text_width))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = bubble_path(r, self.mine)
        if self.mine:
            paint_glass(p, r, 15, tint(ACCENT, 235, 100), tint(ACCENT, 230, 85), tint(ACCENT, 150, 150),
                        tint(ACCENT, 30, 120), rgba(255, 255, 255, 28), path=shape)
        else:
            paint_glass(p, r, 15, rgba(70, 66, 80, 200), rgba(52, 48, 62, 200), rgba(255, 255, 255, 60),
                        rgba(255, 255, 255, 10), rgba(255, 255, 255, 14), SMOKE, path=shape)


def bubble_path(r, mine, radius=16, tail=5):
    """Rounded bubble with one sharp top corner on the speaker's side, like WhatsApp's tail."""
    tl, tr = (radius, tail) if mine else (tail, radius)
    path = QPainterPath()
    path.moveTo(r.left() + tl, r.top())
    path.lineTo(r.right() - tr, r.top())
    path.quadTo(r.right(), r.top(), r.right(), r.top() + tr)
    path.lineTo(r.right(), r.bottom() - radius)
    path.quadTo(r.right(), r.bottom(), r.right() - radius, r.bottom())
    path.lineTo(r.left() + radius, r.bottom())
    path.quadTo(r.left(), r.bottom(), r.left(), r.bottom() - radius)
    path.lineTo(r.left(), r.top() + tl)
    path.quadTo(r.left(), r.top(), r.left() + tl, r.top())
    return path


class ImageBubble(QFrame):
    """A photo or screenshot you sent, shown as a rounded thumbnail inside your bubble."""

    def __init__(self, image, caption=""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 5)
        layout.setSpacing(3)
        thumb = QPixmap.fromImage(image).scaledToWidth(min(240, image.width()), Qt.SmoothTransformation)
        rounded = QPixmap(thumb.size())
        rounded.fill(Qt.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(rounded.rect()), 12, 12)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, thumb)
        p.end()
        picture = QLabel()
        picture.setPixmap(rounded)
        picture.setStyleSheet("background: transparent;")
        layout.addWidget(picture)
        if caption:
            text = QLabel(caption)
            text.setWordWrap(True)
            text.setMaximumWidth(240)
            text.setFont(font(13))
            text.setStyleSheet(f"color: {qss(TEXT)}; background: transparent; padding: 0 6px;")
            layout.addWidget(text)
        stamp = QLabel(time.strftime("%H:%M") + " ✓✓")
        stamp.setFont(font(9))
        stamp.setAlignment(Qt.AlignRight)
        stamp.setStyleSheet("color: rgba(255,255,255,190); background: transparent; padding-right: 6px;")
        layout.addWidget(stamp)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, 15, tint(ACCENT, 235, 100), tint(ACCENT, 230, 85), tint(ACCENT, 150, 150),
                    tint(ACCENT, 30, 120), rgba(255, 255, 255, 28), path=bubble_path(r, True))


GIFS = os.path.join(ASSETS, "gifs")  # one folder per emotion, anime GIFs from nekos.best
GIF_CHANCE = 0.2  # not every reply, or it stops being cute
GIF_COOLDOWN = (3, 8)  # replies to skip after a GIF, picked at random so it feels spontaneous
_gif_wait = random.randint(*GIF_COOLDOWN)


def pick_gif(emotion):
    global _gif_wait
    folder = os.path.join(GIFS, emotion or "")
    if not emotion or emotion == "neutra" or not os.path.isdir(folder):
        return None
    if _gif_wait > 0:
        _gif_wait -= 1
        return None
    if random.random() > GIF_CHANCE:
        return None
    _gif_wait = random.randint(*GIF_COOLDOWN)
    files = [f for f in os.listdir(folder) if f.endswith(".gif")]
    return os.path.join(folder, random.choice(files)) if files else None


class GifBubble(QFrame):
    """An anime GIF Yui drops in to show how she feels, looped inside a glass bubble."""

    def __init__(self, path, width=220):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self.picture = QLabel()
        self.picture.setStyleSheet("background: transparent;")
        layout.addWidget(self.picture)
        self.movie = QMovie(path, parent=self)
        self.movie.jumpToFrame(0)
        size = self.movie.currentImage().size()
        if size.width() > 0:
            self.movie.setScaledSize(QSize(width, int(size.height() * width / size.width())))
        self.picture.setMovie(self.movie)
        self.movie.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 15)


def jpeg_base64(image, longest=1600):
    """Shrink and encode an image the way the AI likes it: JPEG, base64, at most ~1600 px."""
    if max(image.width(), image.height()) > longest:
        image = image.scaled(longest, longest, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.convertToFormat(image.Format.Format_RGB888).save(buffer, "JPG", 82)
    return bytes(data.toBase64()).decode()


class Pill(QLabel):
    """Centered system message, like WhatsApp's date and info chips."""

    def __init__(self, text, color=SUBTEXT):
        super().__init__(text)
        self.setFont(font(11))
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"color: {qss(color)}; background: transparent; padding: 4px 12px;")
        self.text_width = self.fontMetrics().horizontalAdvance(text) + 30
        self.fit(330)

    def fit(self, width):
        self.setMaximumWidth(width)
        self.setMinimumWidth(min(width, self.text_width))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, min(13, r.height() / 2), rgba(255, 255, 255, 20), rgba(255, 255, 255, 8),
                    rgba(255, 255, 255, 50), rgba(255, 255, 255, 8), None, SMOKE)
        p.end()
        super().paintEvent(e)


class PermissionCard(QFrame):
    def __init__(self, what, detail, answer):
        super().__init__()
        self.setMaximumWidth(340)
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(5)
        title = QLabel(f"<span style='font-family:\"{ICON_FONT}\"'>{icon(I_SHIELD)}</span>&nbsp;&nbsp;Posso fazer isso?")
        title.setFont(font(13, QFont.DemiBold))
        title.setStyleSheet(f"color: {qss(WARN)}; background: transparent;")
        layout.addWidget(title)
        body = QLabel(what)
        body.setWordWrap(True)
        body.setFont(font(13))
        body.setStyleSheet(f"color: {qss(TEXT)}; background: transparent;")
        layout.addWidget(body)
        if detail:
            code = QLabel(detail[:300])
            code.setWordWrap(True)
            code.setFont(QFont("Cascadia Mono", 9))
            code.setStyleSheet(f"color: {qss(SUBTEXT)}; background: transparent;")
            layout.addWidget(code)
        self.row = QHBoxLayout()
        self.row.setSpacing(8)
        self.buttons = []
        for text, color, value in (("Pode", rgba(48, 170, 105, 230), True), ("Não", rgba(220, 70, 100, 230), False)):
            b = TextButton(text, color)
            b.clicked.connect(lambda _=False, v=value: answer(v))
            self.row.addWidget(b)
            self.buttons.append(b)
        self.row.addStretch()
        layout.addSpacing(4)
        layout.addLayout(self.row)

    def answered(self, allowed):
        for b in self.buttons:
            b.deleteLater()
        result = QLabel("✓ autorizado" if allowed else "× negado")
        result.setFont(font(12, QFont.DemiBold))
        result.setStyleSheet(f"color: {qss(OK if allowed else DANGER)}; background: transparent;")
        self.row.insertWidget(0, result)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, rgba(250, 204, 21, 30),
                    rgba(250, 204, 21, 10), rgba(250, 204, 21, 170), rgba(250, 204, 21, 40), backing=SMOKE)


class TextButton(QAbstractButton):
    def __init__(self, text, color):
        super().__init__()
        self.setText(text)
        self.color = color
        self.setFixedSize(92, 32)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, 16, QColor(self.color).lighter(120 if self.underMouse() else 108), self.color,
                    rgba(255, 255, 255, 120), rgba(255, 255, 255, 20))
        p.setFont(font(12, QFont.DemiBold))
        p.setPen(TEXT)
        p.drawText(r, Qt.AlignCenter, self.text())


class Chip(QAbstractButton):
    """A quick reply: small glass pill that sends its text when clicked."""

    def __init__(self, text):
        super().__init__()
        self.setText(text)
        self.setFont(font(12))
        self.setFixedHeight(30)
        self.setFixedWidth(self.fontMetrics().horizontalAdvance(text) + 28)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        boost = 20 if self.underMouse() else 0
        paint_glass(p, r, 15, tint(ACCENT, 40 + boost, 130), tint(ACCENT, 14 + boost, 130),
                    tint(ACCENT, 130, 160), tint(ACCENT, 30, 140), backing=SMOKE)
        p.setPen(TEXT)
        p.drawText(r, Qt.AlignCenter, self.text())


def quick_replies(text, emotion):
    """Ready-made answers that fit what Yui just said, for when you don't feel like talking."""
    low = text.lower()
    if "?" in text:
        if any(w in low for w in ("quer que", "posso", "quer ", "prefere", "bora", "vamos")):
            return ["Pode sim", "Não precisa", "Depois a gente vê"]
        return ["Sim", "Não", "Sei lá", "Explica melhor"]
    if emotion == "brava":
        return [f"Foi mal, {hub.NAME}", "Calma aí", "Kkkkk"]
    if emotion == "triste":
        return ["Ei, tá tudo bem", "Valeu mesmo assim", "Tenta de novo"]
    if emotion in ("convencida", "feliz"):
        return ["Kkkkk", f"Valeu, {hub.NAME}!", "Convencida, hein"]
    if emotion == "surpresa":
        return ["Sério?", "Kkkkk", "Pois é"]
    return ["Valeu!", "Beleza", "Kkkkk", "Agora não"]


class Capsule(GlassBar):
    """Floating glass capsule, like the header pieces on WhatsApp for iPhone."""

    def __init__(self, height):
        super().__init__(height / 2)
        self.setFixedHeight(height)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:  # the name capsule also drags the window
            self.window().windowHandle().startSystemMove()


class Header(QWidget):
    """Transparent strip holding the floating capsules. Dragging it moves the window."""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemMove()


class Avatar(QWidget):
    """Round profile photo with a ring that glows in the status color and breathes while she talks or listens."""

    def __init__(self, path, size=38):
        super().__init__()
        self.size = size
        self.setFixedSize(size + 8, size + 8)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked = None  # set by the window: opens the photo / netrunner mark menu
        self._press = None
        self.set_photo(path)
        self.color, self.pulsing, self.phase = None, False, 0.0
        self.timer = QTimer(self, interval=33, timeout=self.tick)

    def set_photo(self, path):
        size = self.size
        pix = QPixmap(path).scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.pix = QPixmap(size, size)
        self.pix.fill(Qt.transparent)
        p = QPainter(self.pix)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)
        p.setClipPath(clip)
        p.drawPixmap((size - pix.width()) // 2, (size - pix.height()) // 2, pix)
        p.end()
        self.update()

    # a click opens the menu; a drag still moves the window like the rest of the capsule
    def mousePressEvent(self, e):
        self._press = e.globalPosition()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.globalPosition() - self._press).manhattanLength() > 6:
            self._press = None
            self.window().windowHandle().startSystemMove()

    def mouseReleaseEvent(self, e):
        if self._press is not None and self.clicked:
            self.clicked()
        self._press = None

    def set_state(self, color, pulsing):
        if color != self.color or pulsing != self.pulsing:
            self.color, self.pulsing = color, pulsing
            self.timer.start() if pulsing else self.timer.stop()
            self.update()

    def tick(self):
        self.phase += 0.16
        self.update()

    def paintEvent(self, e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.color is not None:
            beat = 0.5 + 0.5 * math.sin(self.phase) if self.pulsing else 0.35
            glow = QRadialGradient(c, self.width() / 2)
            glow.setColorAt(0.75, tint(self.color, int(40 + 110 * beat)))
            glow.setColorAt(1, CLEAR)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(c, self.width() / 2, self.height() / 2)
            p.setPen(QPen(tint(self.color, int(150 + 100 * beat)), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, self.size / 2 + 1.5, self.size / 2 + 1.5)
        p.drawPixmap(4, 4, self.pix)


class TypingBubble(QFrame):
    """Yui's "..." bubble with bouncing dots while she thinks."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(62, 34)
        self.phase = 0.0
        self.timer = QTimer(self, interval=40, timeout=self.tick)
        self.timer.start()

    def tick(self):
        self.phase += 0.22
        self.update()

    def paintEvent(self, e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, 15, rgba(70, 66, 80, 200), rgba(52, 48, 62, 200), rgba(255, 255, 255, 60),
                    rgba(255, 255, 255, 10), rgba(255, 255, 255, 14), SMOKE, path=bubble_path(r, False))
        p.setPen(Qt.NoPen)
        for i in range(3):
            wave = max(0.0, math.sin(self.phase - i * 0.9))
            p.setBrush(tint(TEXT, int(110 + 140 * wave)))
            p.drawEllipse(QPointF(19 + i * 12, 17 - 4 * wave), 3.6, 3.6)


class MessageField(GlassBar):
    """The message box: lights up in the accent color while you type."""

    focused = False

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.focused:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(tint(ACCENT, 200, 125), 1.4))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), self.radius - 1, self.radius - 1)


class CallButton(QAbstractButton):
    """Big round button on the call screen, with its label underneath."""

    def __init__(self, glyph, label, fill=None):
        super().__init__()
        self.glyph, self.label, self.fill, self.on = glyph, label, fill, False
        self.setFixedSize(76, 86)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(8, 2, 60, 60)
        hover = self.underMouse()
        if self.fill is not None:
            paint_glass(p, r, 30, QColor(self.fill).lighter(125 if hover else 110), self.fill,
                        rgba(255, 255, 255, 120), rgba(255, 255, 255, 20))
        elif self.on:  # toggled (muted): solid white like iPhone
            paint_glass(p, r, 30, rgba(255, 255, 255, 240), rgba(235, 230, 240, 230), rgba(255, 255, 255, 255),
                        rgba(255, 255, 255, 80), None)
        else:
            boost = 22 if hover else 0
            paint_glass(p, r, 30, rgba(255, 255, 255, 34 + boost), rgba(255, 255, 255, 12 + boost),
                        rgba(255, 255, 255, 90), rgba(255, 255, 255, 14), backing=SMOKE)
        f = QFont(ICON_FONT)
        f.setPixelSize(24)
        p.setFont(f)
        p.setPen(rgba(30, 20, 40) if self.on and self.fill is None else TEXT)
        p.drawText(r, Qt.AlignCenter, icon(self.glyph))
        p.setFont(font(11))
        p.setPen(SUBTEXT)
        p.drawText(QRectF(0, 66, 76, 18), Qt.AlignCenter, self.label)


class CallView(QWidget):
    """Full-window call screen: her photo big in the middle, waves that follow whoever is talking,
    call timer, live caption of the last thing said, and mute / chat / hang-up buttons."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.started = time.time()
        self.phase, self.level, self.opacity = 0.0, 0.0, 0.0
        self.caption, self.caption_mine = "", False
        self.waves = []  # (birth phase, strength) of rings rippling out of the photo
        self.photo = None
        self.set_photo(win.photo_path())
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 0, 20, 34)
        col.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch()
        self.mute = CallButton(I_MIC_OFF, "mudo")
        self.mute.clicked.connect(self.toggle_mute)
        self.chat = CallButton("", "chat")
        self.chat.clicked.connect(win.minimize_call)
        hang = CallButton(I_HANG_UP, "desligar", rgba(235, 64, 90, 240))
        hang.clicked.connect(win.toggle_call)
        for b in (self.mute, self.chat, hang):
            row.addWidget(b)
        row.addStretch()
        col.addLayout(row)
        self.timer = QTimer(self, interval=33, timeout=self.tick)

    def set_photo(self, path):
        size = 168
        pix = QPixmap(path).scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.photo = QPixmap(size, size)
        self.photo.fill(Qt.transparent)
        p = QPainter(self.photo)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)
        p.setClipPath(clip)
        p.drawPixmap((size - pix.width()) // 2, (size - pix.height()) // 2, pix)
        p.end()

    def open(self):
        self.started = time.time()
        self.caption = ""
        hub.call_muted.clear()
        self.mute.on = False
        self.reveal()

    def reveal(self):
        self.setGeometry(self.win.rect())
        self.opacity = 0.0
        self.show()
        self.raise_()
        self.timer.start()

    def toggle_mute(self):
        self.mute.on = not self.mute.on
        hub.call_muted.set() if self.mute.on else hub.call_muted.clear()
        self.mute.label = "mutado" if self.mute.on else "mudo"
        self.mute.update()

    def say(self, text, mine):
        self.caption, self.caption_mine = text, mine
        self.update()

    def tick(self):
        y = self.win.yui
        self.opacity = min(1.0, self.opacity + 0.08)
        talking = y is not None and not y.voice.idle.is_set()
        raw = 0.0
        if talking:
            raw = min(1.0, getattr(y.voice, "level", 0.0) * 6)
        elif y is not None and hasattr(y, "mic") and not hub.call_muted.is_set():
            raw = min(1.0, getattr(y.mic, "level", 0.0) * 14)
        self.level += (raw - self.level) * 0.35
        self.phase += 0.033
        if self.level > 0.12 and (not self.waves or self.phase - self.waves[-1][0] > 0.28):
            self.waves.append((self.phase, self.level))
        self.waves = [w for w in self.waves if self.phase - w[0] < 1.6]
        self.update()

    def paintEvent(self, e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        w, h = self.width(), self.height()
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.setClipPath(clip)
        # deep tinted glass over the chat, with the accent pooling behind her photo
        p.fillPath(clip, rgba(12, 6, 22, 228))
        y = self.win.yui
        talking = y is not None and not y.voice.idle.is_set()
        color = ACCENT if talking else (SUBTEXT if hub.call_muted.is_set() else OK)
        c = QPointF(w / 2, h * 0.36)
        glow = QRadialGradient(c, w * 0.85)
        glow.setColorAt(0, tint(ACCENT, int(90 + 70 * self.level)))
        glow.setColorAt(0.5, tint(ACCENT, 25))
        glow.setColorAt(1, CLEAR)
        p.fillRect(rect, QBrush(glow))
        # top: name, status and timer
        p.setPen(TEXT)
        p.setFont(font(26, QFont.DemiBold))
        p.drawText(QRectF(0, 44, w, 36), Qt.AlignCenter, hub.NAME)
        secs = int(time.time() - self.started)
        if hub.call_muted.is_set():
            state = "mutado"
        elif talking:
            state = "falando"
        elif y is not None and y.busy:
            state = "pensando"
        elif y is not None and time.time() < y.listen_until:
            state = "ouvindo"
        else:
            state = f"diga \"{hub.NAME}\" pra falar"
        p.setFont(font(13))
        p.setPen(SUBTEXT)
        p.drawText(QRectF(0, 82, w, 22), Qt.AlignCenter, f"{secs // 60:02d}:{secs % 60:02d}  ·  {state}")
        # ripples
        base = 84
        p.setBrush(Qt.NoBrush)
        for born, strength in self.waves:
            age = (self.phase - born) / 1.6
            p.setPen(QPen(tint(color, int(200 * (1 - age) * (0.4 + strength))), 2))
            r = base + 6 + age * 90
            p.drawEllipse(c, r, r)
        # breathing halo + ring
        breath = 0.5 + 0.5 * math.sin(self.phase * 2.2)
        halo = QRadialGradient(c, base + 30 + 22 * self.level)
        halo.setColorAt(0.7, tint(color, int(70 + 60 * breath + 100 * self.level)))
        halo.setColorAt(1, CLEAR)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, base + 30 + 22 * self.level, base + 30 + 22 * self.level)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(tint(color, 230), 3))
        p.drawEllipse(c, base + 3 + 4 * self.level, base + 3 + 4 * self.level)
        p.drawPixmap(int(c.x() - base), int(c.y() - base), self.photo)
        # equalizer bars under the photo
        bars, bw = 15, 5
        x0 = w / 2 - (bars * (bw + 4)) / 2
        for i in range(bars):
            wobble = 0.5 + 0.5 * math.sin(self.phase * 9 + i * 1.3) * math.sin(self.phase * 4.1 + i * 0.7)
            hgt = 4 + 30 * self.level * wobble * (1 - abs(i - bars // 2) / bars)
            p.setBrush(tint(color, 220))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x0 + i * (bw + 4), c.y() + base + 44 - hgt / 2, bw, hgt), 2.5, 2.5)
        # caption of the last line said
        if self.caption:
            p.setFont(font(14))
            p.setPen(tint(TEXT, 235) if not self.caption_mine else SUBTEXT)
            text = self.caption if len(self.caption) < 170 else self.caption[:167] + "..."
            who = "você: " if self.caption_mine else ""
            p.drawText(QRectF(30, c.y() + base + 70, w - 60, h - (c.y() + base + 70) - 130),
                       Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, who + text)
        p.setClipping(False)
        p.setPen(QPen(WINDOW_BORDER, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(clip)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemMove()


class CallChip(QAbstractButton):
    """Green "em ligação" pill on top of the chat while the call runs in the background."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setFixedSize(150, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(win.call_view.reveal)
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, 15, rgba(48, 170, 105, 235), rgba(36, 140, 88, 235), rgba(255, 255, 255, 120),
                    rgba(255, 255, 255, 20))
        secs = int(time.time() - self.win.call_view.started)
        p.setPen(TEXT)
        p.setFont(font(12, QFont.DemiBold))
        p.drawText(r, Qt.AlignCenter, f"em ligação  {secs // 60:02d}:{secs % 60:02d}")


class SheetTile(QAbstractButton):
    """Round colored icon with a caption, like the iPhone's attachment sheet."""

    def __init__(self, glyph, label, color):
        super().__init__()
        self.glyph, self.label, self.color = glyph, label, QColor(color)
        self.setFixedSize(78, 86)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse() and self.isEnabled()
        size = 54 if hover else 50
        r = QRectF((self.width() - size) / 2, 6 + (50 - size) / 2, size, size)
        if hover:  # soft halo in the tile's own color
            halo = QRadialGradient(r.center(), size * 0.9)
            halo.setColorAt(0, tint(self.color, 110))
            halo.setColorAt(1, CLEAR)
            p.fillRect(self.rect(), QBrush(halo))
        alpha = 255 if self.isEnabled() else 70
        paint_glass(p, r, size / 2, tint(self.color, alpha, 125), tint(self.color, alpha, 85),
                    rgba(255, 255, 255, 140), rgba(255, 255, 255, 20))
        f = QFont(ICON_FONT)
        f.setPixelSize(22)
        p.setFont(f)
        p.setPen(rgba(255, 255, 255, alpha))
        p.drawText(r, Qt.AlignCenter, icon(self.glyph))
        p.setFont(font(11, QFont.Medium))
        p.setPen(TEXT if self.isEnabled() else SUBTEXT)
        p.drawText(QRectF(0, 62, self.width(), 22), Qt.AlignHCenter | Qt.AlignTop, self.label)


class AttachSheet(QWidget):
    """The + button's drawer: a frosted panel that slides up with icon tiles and the wallpaper slider."""

    def __init__(self, window):
        super().__init__(window, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.window_ = window
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 14)
        col.setSpacing(6)
        title = QLabel("Anexar e personalizar")
        title.setFont(font(12, QFont.DemiBold))
        title.setStyleSheet(f"color: {qss(SUBTEXT)}; background: transparent; padding-left: 4px;")
        col.addWidget(title)
        has_wall = window.wallpaper is not None
        rows = [[("", "Foto", PINK, window.send_photo),
                 ("", "Print", CYAN, window.send_screenshot),
                 ("", "Fundo", PURPLE, window.choose_wallpaper)],
                [("", "Tirar fundo", DANGER, window.remove_wallpaper if has_wall else None),
                 ("", "Configurar", rgba(150, 140, 170), window.open_settings)]]
        for items in rows:
            line = QHBoxLayout()
            line.setSpacing(4)
            for glyph, label, color, action in items:
                tile = SheetTile(glyph, label, color)
                tile.setEnabled(action is not None)
                if action is not None:
                    tile.clicked.connect(lambda _=False, a=action: self.run(a))
                line.addWidget(tile)
            col.addLayout(line)
        if has_wall:
            label = QLabel("Fundo:  mais escuro  ↔  mais claro")
            label.setFont(font(11))
            label.setStyleSheet(f"color: {qss(SUBTEXT)}; background: transparent; padding: 4px 4px 0;")
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(window.settings.get("shade", 0))
            slider.setStyleSheet(
                "QSlider::groove:horizontal { height: 6px; border-radius: 3px;"
                " background: qlineargradient(x1:0, x2:1, stop:0 #1a0f26, stop:1 #fff2f9); }"
                "QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;"
                f" background: {ACCENT.name()}; border: 2px solid white; }}")
            slider.valueChanged.connect(window.set_shade)
            slider.sliderReleased.connect(window.save_settings)
            col.addWidget(label)
            col.addWidget(slider)
        self.adjustSize()

    def run(self, action):
        self.close()
        QTimer.singleShot(0, action)

    def popup(self, anchor):
        end = anchor.mapToGlobal(anchor.rect().topLeft())
        end.setY(end.y() - self.height() - 8)
        start = QPointF(end.x(), end.y() + 24).toPoint()
        self.move(start)
        self.setWindowOpacity(0)
        self.show()
        self.slide = QPropertyAnimation(self, b"pos", self)
        self.slide.setDuration(260)
        self.slide.setStartValue(start)
        self.slide.setEndValue(end)
        self.slide.setEasingCurve(QEasingCurve.OutBack)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(180)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.slide.start()
        self.fade.start()

    def closeEvent(self, e):
        self.window_.save_settings()
        super().closeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, 22, rgba(255, 255, 255, 26), rgba(255, 255, 255, 10), rgba(255, 255, 255, 90),
                    rgba(255, 255, 255, 14), backing=rgba(30, 18, 46, 238))
        glow = QRadialGradient(QPointF(r.width() * 0.2, 0), r.width() * 0.8)
        glow.setColorAt(0, tint(ACCENT, 45))
        glow.setColorAt(1, CLEAR)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        p.fillPath(path, QBrush(glow))


class Bridge(QObject):
    event = Signal(str, object)


# --- window -------------------------------------------------------------------------------------
class ChatWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(hub.NAME)
        self.setWindowIcon(QIcon(os.path.join(ASSETS, "maidme.ico")))
        self.resize(430, 780)
        self.setMinimumSize(320, 420)
        self.yui = None
        self.permission = None
        self.typing = self.typing_line = None
        self.mood = None  # color of Yui's last emotion, glowing softly under the header
        self.acrylic = False
        self.settings = self.load_settings()
        self.wallpaper = None
        self.load_wallpaper()
        self.bridge = Bridge()
        self.bridge.event.connect(self.on_event)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = self.build_header()
        root.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 6px; background: transparent; margin: 4px 2px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,60); border-radius: 3px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")
        holder = QWidget()
        self.chat = QVBoxLayout(holder)
        self.chat.setContentsMargins(12, 12, 12, 12)
        self.chat.setSpacing(6)
        self.chat.addStretch()
        self.scroll.setWidget(holder)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _, maximum: self.scroll.verticalScrollBar().setValue(maximum))
        root.addWidget(self.scroll, 1)
        self.input_bar = self.build_input()
        root.addWidget(self.input_bar)
        self.add_center(Pill("Hoje"))
        self.call_view = CallView(self)
        self.call_view.hide()
        self.call_chip = CallChip(self)

        self.status_timer = QTimer(self, interval=200, timeout=self.update_status)
        self.status_timer.start()

    # --- layout ---------------------------------------------------------------------
    def build_header(self):
        bar = Header()
        bar.setFixedHeight(70)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 12, 12, 8)
        row.setSpacing(8)
        who = Capsule(48)
        who_row = QHBoxLayout(who)
        who_row.setContentsMargins(1, 1, 18, 1)
        who_row.setSpacing(7)
        self.avatar = Avatar(self.photo_path())
        self.avatar.clicked = self.show_profile_menu
        who_row.addWidget(self.avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        name = self.name_label = QLabel(hub.NAME)
        name.setFont(font(14, QFont.DemiBold))
        name.setStyleSheet(f"color: {qss(TEXT)}; background: transparent;")
        self.status = QLabel("acordando...")
        self.status.setFont(font(11))
        self.status.setStyleSheet(f"color: {qss(SUBTEXT)}; background: transparent;")
        names.addStretch()
        names.addWidget(name)
        names.addWidget(self.status)
        names.addStretch()
        who_row.addLayout(names)
        row.addWidget(who)
        row.addStretch()
        tools = Capsule(48)
        tools_row = QHBoxLayout(tools)
        tools_row.setContentsMargins(6, 0, 6, 0)
        tools_row.setSpacing(2)
        self.mic_button = GlyphButton(I_PHONE, 38, f"Ligar pra {hub.NAME}", flat=True)
        self.mic_button.clicked.connect(self.toggle_call)
        stop = GlyphButton(I_STOP, 38, "Mandar ela calar a boca", flat=True)
        stop.clicked.connect(lambda: self.send_event("stop"))
        for b in (self.mic_button, stop):
            tools_row.addWidget(b)
        row.addWidget(tools)
        window_buttons = QVBoxLayout()
        window_buttons.setSpacing(4)
        close = GlyphButton(I_CLOSE, 22, "Fechar")
        close.clicked.connect(self.close)
        minimize = GlyphButton(I_MIN, 22, "Minimizar")
        minimize.clicked.connect(self.showMinimized)
        window_buttons.addWidget(close)
        window_buttons.addWidget(minimize)
        row.addSpacing(4)
        row.addLayout(window_buttons)
        return bar

    def set_quick_replies(self, replies):
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for text in replies:
            chip = Chip(text)
            chip.clicked.connect(lambda _=False, t=text: self.send_text(t))
            self.chips.addWidget(chip)
        self.chips.addStretch()

    def build_input(self):
        bar = QWidget()
        column = QVBoxLayout(bar)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.chips = QHBoxLayout()
        self.chips.setContentsMargins(12, 6, 12, 0)
        self.chips.setSpacing(6)
        column.addLayout(self.chips)
        row = QHBoxLayout()
        column.addLayout(row)
        row.setContentsMargins(12, 8, 12, 14)
        row.setSpacing(8)
        plus = GlyphButton("\uE710", 42, "Papel de parede e configurações")
        plus.clicked.connect(lambda: AttachSheet(self).popup(plus))
        row.addWidget(plus)
        field = self.field = MessageField(21)
        field.setFixedHeight(42)
        inner = QHBoxLayout(field)
        inner.setContentsMargins(16, 0, 10, 0)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Mensagem")
        self.entry.setFont(font(13))
        self.entry.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {qss(TEXT)}; "
                                 f"selection-background-color: {qss(PINK)}; }}")
        self.entry.returnPressed.connect(self.send_typed)
        self.entry.installEventFilter(self)
        inner.addWidget(self.entry)
        row.addWidget(field, 1)
        # like WhatsApp: with an empty box the send button is a mic, hold it to record a voice message
        send = self.send_button = GlyphButton(I_MIC, 42, "Segure pra mandar áudio", fill=tint(ACCENT, 235, 95))
        send.pressed.connect(self.press_send)
        send.released.connect(self.release_send)
        self.recording = False
        self.entry.textChanged.connect(self.update_send_glyph)
        row.addWidget(send)
        grip = QSizeGrip(self)
        grip.setStyleSheet("background: transparent;")
        grip.setFixedSize(12, 12)
        row.addWidget(grip, 0, Qt.AlignBottom)
        return bar

    def add_row(self, widget, align):
        if isinstance(widget, (Bubble, Pill)):
            widget.fit(self.bubble_width() + (40 if isinstance(widget, Pill) else 0))
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        if align != Qt.AlignLeft:
            line.addStretch()
        line.addWidget(widget)
        if align != Qt.AlignRight:
            line.addStretch()
        self.chat.addLayout(line)
        if not isinstance(widget, TypingBubble):
            # new messages fade in instead of popping
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", widget)
            anim.setDuration(280)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda w=widget: QTimer.singleShot(0, lambda: w.setGraphicsEffect(None)))
            anim.start(QAbstractAnimation.DeleteWhenStopped)
        return line

    def add_center(self, widget):
        self.add_row(widget, Qt.AlignCenter)

    # --- events from Yui (via the bridge, on the GUI thread) ----------------------------
    def on_event(self, kind, args):
        self.hide_typing()  # anything new goes above the dots; they come back on the next status tick
        if kind == "yui" and args[1] in EMOTIONS:
            self.mood = EMOTIONS[args[1]][1]
            self.update()
        if kind in ("you", "yui") and self.call_view.isVisible():
            self.call_view.say(args[0], kind == "you")
        if kind == "you":
            self.add_row(Bubble(args[0], True), Qt.AlignRight)
            self.set_quick_replies([])
        elif kind == "yui":
            self.add_row(Bubble(args[0], False, args[1]), Qt.AlignLeft)
            gif = pick_gif(args[1])
            if gif:
                self.add_row(GifBubble(gif), Qt.AlignLeft)
            self.set_quick_replies(quick_replies(args[0], args[1]))
        elif kind == "tool":
            self.add_center(Pill(args[0], CYAN))
        elif kind == "note" and not args[0].startswith("(voz)"):
            self.add_center(Pill(args[0]))
        elif kind == "ask":
            self.permission = PermissionCard(args[1], args[2], lambda v: self.send_event("permission", v))
            self.add_center(self.permission)
            self.set_quick_replies([])  # the card has its own buttons
        elif kind == "answer" and self.permission:
            self.permission.answered(args[0])
            self.permission = None
        elif kind == "info":
            self.add_center(Pill(f"{args[0]}: {args[1]}"))
        elif kind == "error":
            self.add_center(Pill(f"{args[0]}: {args[1]}", DANGER))

    def update_status(self):
        y = self.yui
        if y is None:
            text, color = "acordando...", SUBTEXT
        elif y.pending:
            text, color = "esperando você responder", WARN
        elif not y.voice.idle.is_set():
            text, color = "falando...", ACCENT
        elif y.busy:
            text, color = "digitando...", PURPLE
        elif self.recording:
            text, color = "gravando seu áudio...", DANGER
        elif hub.in_call.is_set() and time.time() < y.listen_until:
            text, color = "ouvindo, pode falar", OK
        elif hub.in_call.is_set():
            text, color = "em ligação", OK
        else:
            text, color = "online", OK
        if self.call_chip.isVisible():
            self.call_chip.update()
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {qss(color)}; background: transparent;")
        self.avatar.set_state(color if y is not None else None, text in ("falando...", "ouvindo, pode falar"))
        thinking = y is not None and y.busy and not y.pending and y.voice.idle.is_set()
        if thinking and self.typing is None:
            self.typing = TypingBubble()
            self.typing_line = self.add_row(self.typing, Qt.AlignLeft)
        elif not thinking:
            self.hide_typing()

    def hide_typing(self):
        if self.typing is not None:
            self.typing.timer.stop()
            self.typing.deleteLater()
            self.chat.removeItem(self.typing_line)
            self.typing = self.typing_line = None

    # --- actions ------------------------------------------------------------------------
    def send_event(self, kind, data=None):
        if self.yui:
            self.yui.events.put((kind, data))

    def send_typed(self):
        if self.send_text(self.entry.text().strip()):
            self.entry.clear()

    def send_text(self, text):
        if text and self.yui:
            self.yui.events.put(("typed", text))
            return True
        return False

    def send_image(self, image):
        if image.isNull() or not self.yui:
            return
        caption = self.entry.text().strip()
        self.entry.clear()
        self.add_row(ImageBubble(image, caption), Qt.AlignRight)
        self.set_quick_replies([])
        self.yui.events.put(("image", {"data": jpeg_base64(image), "caption": caption}))

    def send_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, f"Mandar foto pra {hub.NAME}", os.path.expanduser("~\\Pictures"),
                                              "Imagens (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if path:
            from PySide6.QtGui import QImage
            self.send_image(QImage(path))

    def send_screenshot(self):
        # get out of the way, grab the screen the window is on, then come back
        screen = self.screen()
        self.hide()

        def grab():
            shot = screen.grabWindow(0).toImage()
            self.show()
            self.raise_()
            self.send_image(shot)
        QTimer.singleShot(350, grab)

    def toggle_call(self):
        if not self.yui or not hasattr(self.yui, "mic"):
            return
        if hub.in_call.is_set():
            secs = int(time.time() - self.call_view.started)
            hub.in_call.clear()
            hub.call_muted.clear()
            self.yui.listen_until = 0
            hub.note(f"ligação encerrada · {secs // 60:02d}:{secs % 60:02d}")
            self.mic_button.glyph, self.mic_button.fill = I_PHONE, None
            self.mic_button.setToolTip(f"Ligar pra {hub.NAME}")
            self.call_view.timer.stop()
            self.call_view.hide()
            self.call_chip.hide()
        else:
            hub.in_call.set()
            hub.note("ligação iniciada")
            self.mic_button.glyph, self.mic_button.fill = I_HANG_UP, rgba(220, 70, 100, 230)
            self.mic_button.setToolTip("Desligar")
            self.call_view.open()
            self.call_chip.hide()
            try:
                from assistente import sfx
                sfx.play("open")
            except Exception:
                pass
        self.mic_button.update()

    def minimize_call(self):
        """Back to the chat with the call still running; the green pill brings the call screen back."""
        self.call_view.timer.stop()
        self.call_view.hide()
        self.place_call_chip()
        self.call_chip.show()
        self.call_chip.raise_()

    def place_call_chip(self):
        self.call_chip.move((self.width() - self.call_chip.width()) // 2, self.header.height() + 6)

    def eventFilter(self, obj, e):
        if obj is self.entry and e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            self.field.focused = e.type() == e.Type.FocusIn
            self.field.update()
        return super().eventFilter(obj, e)

    def update_send_glyph(self):
        typing = bool(self.entry.text().strip())
        self.send_button.glyph = I_SEND if typing else I_MIC
        self.send_button.setToolTip("Enviar" if typing else "Segure pra mandar áudio")
        self.send_button.update()

    def press_send(self):
        if self.entry.text().strip() or not self.yui or not hasattr(self.yui, "mic"):
            return
        self.recording = True
        self.yui.mic.start_note()
        self.send_button.fill = rgba(220, 70, 100, 235)
        self.send_button.update()
        self.update_status()

    def release_send(self):
        if not self.recording:
            self.send_typed()
            return
        self.recording = False
        self.send_button.fill = tint(ACCENT, 235, 95)
        self.send_button.update()
        pcm = self.yui.mic.stop_note()
        if pcm is None:
            hub.note("áudio curto demais, segure enquanto fala")
        else:
            self.yui.voice_note(pcm)
        self.update_status()

    # --- wallpaper ----------------------------------------------------------------------
    def load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8-sig") as f:
                return {"wallpaper": "", "shade": -40, **json.load(f)}
        except (OSError, ValueError):
            return {"wallpaper": "", "shade": -40}

    def save_settings(self):
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=2)

    def load_wallpaper(self):
        path = self.settings.get("wallpaper")
        pix = QPixmap(path) if path and os.path.isfile(path) else None
        self.wallpaper = pix if pix is not None and not pix.isNull() else None
        self.apply_accent(accent_from(self.wallpaper) if self.wallpaper is not None else DEFAULT_ACCENT)

    def apply_accent(self, color):
        global ACCENT
        ACCENT = color
        if hasattr(self, "send_button"):
            self.send_button.fill = tint(ACCENT, 235, 95)
        self.update()
        for child in self.findChildren(QWidget):
            child.update()

    def choose_wallpaper(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolher papel de parede", os.path.expanduser("~\\Pictures"),
                                              "Imagens (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if path:
            self.settings["wallpaper"] = path
            self.save_settings()
            self.load_wallpaper()

    # --- profile photo and netrunner mark ------------------------------------------------
    def photo_path(self):
        path = self.settings.get("photo") or ""
        return path if os.path.isfile(path) else os.path.join(ASSETS, "logo.png")

    def sigil_path(self):
        path = self.settings.get("sigil") or ""
        return path if os.path.isfile(path) else os.path.join(SIGILS, "assinatura.svg")

    def show_profile_menu(self):
        from assistente.netrun import _neon
        from assistente.sigil_studio import ProfilePanel
        panel = ProfilePanel(self, SIGILS, _neon(ACCENT))
        panel.move(self.avatar.mapToGlobal(QPointF(0, self.avatar.height() + 6).toPoint()))
        panel.show()

    def open_settings(self):
        """Settings panel; saving restarts the app so everything picks up the new setup."""
        from assistente.netrun import _neon
        from assistente.settings_panel import SettingsDialog, restart
        if SettingsDialog(self, _neon(ACCENT)).exec():
            if self.yui:
                self.yui.brain.close()
            restart()

    def show_profile_menu_old(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(40, 26, 58, 245); border: 1px solid rgba(255,210,240,80); border-radius: 12px;"
            f" padding: 6px; color: {qss(TEXT)}; }}"
            f"QMenu::item {{ padding: 7px 18px; border-radius: 8px; }}"
            f"QMenu::item:selected {{ background: {qss(tint(ACCENT, 170))}; }}"
            f"QMenu::separator {{ height: 1px; background: rgba(255,255,255,40); margin: 5px 10px; }}")
        menu.setFont(font(13))
        menu.addAction(f"Trocar foto da {hub.NAME}...", self.choose_photo)
        menu.addAction("Voltar pra foto padrão", lambda: self.set_photo("")).setEnabled(bool(self.settings.get("photo")))
        menu.addSeparator()
        marks = menu.addMenu("Marca de netrunner")
        marks.setStyleSheet(menu.styleSheet())
        current = os.path.normcase(self.sigil_path())
        for name in sorted(os.listdir(SIGILS)):
            if name.lower().endswith(".svg"):
                path = os.path.join(SIGILS, name)
                act = marks.addAction(name[:-4].capitalize(), lambda p=path: self.set_sigil(p))
                act.setCheckable(True)
                act.setChecked(os.path.normcase(path) == current)
        marks.addSeparator()
        marks.addAction("Escolher SVG...", self.choose_sigil)
        marks.addAction("Abrir pasta das marcas", lambda: plat.open_path(SIGILS))
        marks.addAction("Testar marca", lambda: getattr(self, "netrun", None) and self.netrun.preview_sigil())
        menu.exec(self.avatar.mapToGlobal(QPointF(0, self.avatar.height() + 4).toPoint()))

    def choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, f"Escolher foto da {hub.NAME}", os.path.expanduser("~\\Pictures"),
                                              "Imagens (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.set_photo(path)

    def set_photo(self, path):
        self.settings["photo"] = path
        self.save_settings()
        self.avatar.set_photo(self.photo_path())
        self.call_view.set_photo(self.photo_path())
        if getattr(self, "netrun", None):
            self.netrun.set_photo(self.photo_path())

    def choose_sigil(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolher marca (vetor SVG)", os.path.expanduser("~"),
                                              "Vetor SVG (*.svg)")
        if path:
            self.set_sigil(path)

    def set_sigil(self, path):
        self.settings["sigil"] = path
        self.save_settings()
        if getattr(self, "netrun", None):
            self.netrun.set_sigil(self.sigil_path())
            self.netrun.preview_sigil()

    def remove_wallpaper(self):
        self.settings["wallpaper"] = ""
        self.save_settings()
        self.load_wallpaper()

    def set_shade(self, value):
        self.settings["shade"] = value
        self.update()

    def paint_wallpaper(self, p, rect):
        # cover the window like WhatsApp does: fill it all, crop what sticks out, keep the center
        img = self.wallpaper
        scale = max(rect.width() / img.width(), rect.height() / img.height())
        w, h = img.width() * scale, img.height() * scale
        target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, img, QRectF(img.rect()))
        # slider: negative darkens, positive lightens
        shade = self.settings.get("shade", 0)
        veil = rgba(10, 4, 18, int(-shade * 2.4)) if shade < 0 else rgba(255, 245, 252, int(shade * 1.6))
        p.fillRect(rect, veil)

    def show_menu(self, anchor, attach=False):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(40, 26, 58, 245); border: 1px solid rgba(255,210,240,80); border-radius: 12px;"
            f" padding: 6px; color: {qss(TEXT)}; }}"
            f"QMenu::item {{ padding: 7px 18px; border-radius: 8px; }}"
            f"QMenu::item:selected {{ background: {qss(tint(ACCENT, 170))}; }}"
            f"QMenu::item:disabled {{ color: rgba(255,255,255,90); }}"
            f"QMenu::separator {{ height: 1px; background: rgba(255,255,255,40); margin: 5px 10px; }}")
        menu.setFont(font(13))
        if attach:
            menu.addAction("Mandar foto...", self.send_photo)
            menu.addAction("Mandar print da tela", self.send_screenshot)
            menu.addSeparator()
        menu.addAction("Escolher papel de parede...", self.choose_wallpaper)
        remove = menu.addAction("Remover papel de parede", self.remove_wallpaper)
        remove.setEnabled(self.wallpaper is not None)
        if self.wallpaper is not None:
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(18, 6, 18, 8)
            label = QLabel("Fundo:  mais escuro  ↔  mais claro")
            label.setFont(font(11))
            label.setStyleSheet(f"color: {qss(SUBTEXT)}; background: transparent;")
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(self.settings.get("shade", 0))
            slider.setMinimumWidth(220)
            slider.setStyleSheet(
                "QSlider::groove:horizontal { height: 6px; border-radius: 3px;"
                " background: qlineargradient(x1:0, x2:1, stop:0 #1a0f26, stop:1 #fff2f9); }"
                "QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;"
                f" background: {ACCENT.name()}; border: 2px solid white; }}")
            slider.valueChanged.connect(self.set_shade)
            slider.sliderReleased.connect(self.save_settings)
            col.addWidget(label)
            col.addWidget(slider)
            action = QWidgetAction(menu)
            action.setDefaultWidget(box)
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Configurações avançadas (.env)",
                       lambda: subprocess.Popen(["notepad", os.path.join(ROOT, ".env")]))
        menu.aboutToHide.connect(self.save_settings)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # --- window -------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if not self.acrylic:
            self.acrylic = set_acrylic(int(self.winId()), ACRYLIC_TINT)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        p.setClipPath(path)
        w, h = rect.width(), rect.height()
        if self.wallpaper is not None:
            self.paint_wallpaper(p, rect)
        else:
            p.fillRect(rect, WINDOW_TINT if self.acrylic else rgba(22, 14, 34, 245))
            # colour pooling under the glass gives the bubbles something to refract
            for (cx, cy, r), color in zip(((w * 0.1, h * 0.18, 260), (w * 1.0, h * 0.55, 300),
                                           (w * 0.3, h * 1.0, 300)), BLOBS):
                grad = QRadialGradient(QPointF(cx, cy), r)
                grad.setColorAt(0, color)
                grad.setColorAt(1, CLEAR)
                p.fillRect(rect, QBrush(grad))
        if self.mood is not None:  # her mood lights up the room a little
            glow = QRadialGradient(QPointF(w / 2, self.header.height()), w * 0.8)
            glow.setColorAt(0, tint(self.mood, 55))
            glow.setColorAt(1, CLEAR)
            p.fillRect(rect, QBrush(glow))
        # dark bands behind the header and the message bar, like a TV's lower third
        top = self.header.height()
        band = QLinearGradient(0, 0, 0, top + 18)
        band.setColorAt(0, rgba(6, 3, 12, 225))
        band.setColorAt(top / (top + 18), rgba(6, 3, 12, 175))
        band.setColorAt(1, CLEAR)
        p.fillRect(QRectF(0, 0, w, top + 18), QBrush(band))
        bottom = self.input_bar.y()
        band = QLinearGradient(0, bottom - 18, 0, h)
        band.setColorAt(0, CLEAR)
        band.setColorAt(18 / (h - bottom + 18), rgba(6, 3, 12, 175))
        band.setColorAt(1, rgba(6, 3, 12, 225))
        p.fillRect(QRectF(0, bottom - 18, w, h - bottom + 18), QBrush(band))
        p.fillRect(QRectF(0, top, w, 1), rgba(255, 255, 255, 18))  # hairline where the band ends
        p.setClipping(False)
        p.setPen(QPen(WINDOW_BORDER, 1))
        p.drawPath(path)

    def bubble_width(self):
        return max(180, int(self.width() * 0.72) - 30)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "call_view"):
            self.call_view.setGeometry(self.rect())
            self.place_call_chip()
        width = self.bubble_width()
        for item in self.findChildren(QWidget):
            if isinstance(item, (Bubble, Pill)):
                item.fit(width if isinstance(item, Bubble) else width + 40)

    def nativeEvent(self, event_type, message):
        # frameless window: tell Windows which border the mouse is on, so it resizes from every edge and corner
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes as wt
            msg = wt.MSG.from_address(int(message))
            if msg.message == 0x0084 and not self.isMaximized():  # WM_NCHITTEST
                from PySide6.QtGui import QCursor
                pos = self.mapFromGlobal(QCursor.pos())  # Qt does the DPI math
                edge = 7
                left, right = pos.x() < edge, pos.x() > self.width() - edge
                top, bottom = pos.y() < edge, pos.y() > self.height() - edge
                hit = {(True, False, True, False): 13, (False, True, True, False): 14,
                       (True, False, False, True): 16, (False, True, False, True): 17,
                       (True, False, False, False): 10, (False, True, False, False): 11,
                       (False, False, True, False): 12, (False, False, False, True): 15}.get((left, right, top, bottom))
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def closeEvent(self, e):
        hub.sink = None
        if self.yui:
            try:
                self.yui.brain.close()
            except Exception:
                pass
        super().closeEvent(e)
        os._exit(0)


FONT_FAMILY = "Segoe UI"


def run_app(make_yui):
    global FONT_FAMILY
    plat.app_id("MaidMe.Assistente")  # own taskbar icon
    app = QApplication.instance() or QApplication(sys.argv)
    FONT_FAMILY = load_fonts()
    window = ChatWindow()
    hub.sink = lambda kind, *args: window.bridge.event.emit(kind, args)
    from assistente.settings_panel import SettingsDialog, configured
    if not configured():  # first run: set up before anything else starts
        from assistente.netrun import _neon
        window.show()
        if not SettingsDialog(window, _neon(ACCENT), first_run=True).exec():
            return
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"), override=True)
        hub.NAME = os.getenv("ASSISTANT_NAME", "Assistente")
        hub.GENDER = os.getenv("ASSISTANT_GENDER", "feminino")
        window.setWindowTitle(hub.NAME)
        window.name_label.setText(hub.NAME)
    try:
        from assistente import sfx
        sfx.enabled = window.settings.get("sfx_on", True)
        from assistente import eyes
        eyes.set_enabled(window.settings.get("eyes_on", True))
        sfx.set_volume(window.settings.get("sfx_volume", 25) / 100)
        from assistente.netrun import NetrunPanel
        if os.getenv("NETRUN", "1") == "0":
            raise RuntimeError("efeitos desligados nas configurações")
        window.netrun = NetrunPanel(window.photo_path(), FONT_FAMILY, accent=lambda: ACCENT, sigil_path=window.sigil_path(),
                                    level=lambda: window.yui.voice.level if window.yui else 0.0)
        window.bridge.event.connect(window.netrun.handle)
    except Exception as e:
        print(f"[netrun] {e}", flush=True)
    window.show()

    def start():
        try:
            window.yui = make_yui()
            window.yui.run()
        except SystemExit as e:
            hub.error("yui", str(e))
        except Exception as e:
            hub.error("yui", f"{e.__class__.__name__}: {e}")
            raise
    threading.Thread(target=start, daemon=True).start()
    app.exec()
