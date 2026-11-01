"""Cyberpunk-style "netrunner" holocall: pops up in a screen corner while Yui talks or acts.

A pixelated hologram of Yui (like T-Bug calling V) plus a glitchy log line. The neon color
follows the app wallpaper's accent. When she acts on something, her netrunner mark (any SVG,
see assets/sigils) is stamped over the window in focus, like a hacker tag.
"""
import colorsys
import os
import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from assistente import hub, plat, sfx

FALLBACK = QColor(0, 240, 255)  # netrunner cyan
YELLOW = QColor(252, 238, 10)  # 2077 highlight yellow
W, H = 440, 176
PAD = 8  # room for the glow around the frame
PORTRAIT = 124
GLYPHS = "01#$%&@!?<>/\\|=+*"


def _neon(c):
    h, s, _ = colorsys.rgb_to_hsv(c.redF(), c.greenF(), c.blueF())
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.7), 1.0)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def _hologram(path, size):
    """Yui's picture as a pixelated, high-contrast white mask (tinted at draw time)."""
    img = QImage(path)
    if img.isNull():
        return None
    small = img.scaled(size // 3, size // 3, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    small = small.convertToFormat(QImage.Format_ARGB32)
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            lum = (c.red() * 0.3 + c.green() * 0.59 + c.blue() * 0.11) / 255
            lum = min(1.0, max(0.0, (lum - 0.15) * 1.5)) ** 0.8
            small.setPixelColor(x, y, QColor(255, 255, 255, int(c.alpha() * (0.12 + 0.88 * lum))))
    return small.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.FastTransformation)


def _tint(mask, color):
    out = QImage(mask)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out


def _sigil(path, size):
    """Render an SVG mark as a white mask (tinted at draw time)."""
    renderer = QSvgRenderer(path) if path else None
    if renderer is None or not renderer.isValid():
        return None
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(img.rect(), Qt.white)
    p.end()
    return img


def _hide_from_capture(widget):
    # visible on screen, invisible to screenshots (so she never prints her own effects)
    plat.hide_from_capture(widget)


def _alpha(c, a):
    c = QColor(c)
    c.setAlpha(a)
    return c


class NetrunPanel(QWidget):
    def __init__(self, avatar_path, font_family="Consolas", accent=None, level=None, sigil_path=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self.mask = _hologram(avatar_path, PORTRAIT)
        self.sigil_mask = _sigil(sigil_path, 16)
        self.stamp = SigilStamp(sigil_path)
        self.accent = accent  # callable returning the wallpaper accent color
        self.level = level  # callable returning the loudness of Yui's voice right now
        self.voice = 0.0
        self.bars = [0.0] * 14
        self._set_color(FALLBACK)
        self.title, self.line, self.shown_chars = "", "", 0
        self.t, self.opacity, self.target = 0, 0.0, 0.0
        self.glitch = 0
        self.phase, self.pt = "idle", 0  # open / idle / close animation and its frame count
        self.busy_since = 0.0  # a tool is running and Yui hasn't answered yet
        self.font_mono = QFont("Consolas", 9)
        self.font_small = QFont("Consolas", 7)
        self.font_head = QFont("Consolas", 8, QFont.Bold)
        self.font_title = QFont(font_family, 12, QFont.Bold)
        self.font_title.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        self.hide_timer = QTimer(self, singleShot=True, timeout=self._fade_out)
        self.tick = QTimer(self, interval=33, timeout=self._step)

    def _set_color(self, color):
        self.color = color
        self.holo = _tint(self.mask, color) if self.mask else None
        self.holo_red = _tint(self.mask, QColor(255, 40, 90)) if self.mask else None
        self.sigil_img = _tint(self.sigil_mask, QColor(8, 8, 12)) if self.sigil_mask else None

    def set_photo(self, path):
        self.mask = _hologram(path, PORTRAIT)
        self._set_color(self.color)

    def set_sigil(self, path):
        self.sigil_mask = _sigil(path, 16)
        self.stamp.set_sigil(path)
        self._set_color(self.color)

    def preview_sigil(self):
        self._pop("MARCA CARREGADA", "nova assinatura de netrunner instalada.", keep=5000)
        self.stamp.fire(self.color, force=True)

    # --- events from hub ------------------------------------------------------
    def handle(self, kind, args):
        if kind != "tool":
            self.busy_since = 0.0
        if kind == "tool":
            self._pop("ACESSANDO SISTEMA", args[0], keep=9000)
            self.busy_since = time.time()
            self.stamp.fire(self.color, note=args[0] or "")
        elif kind == "yui":
            text = args[0] or ""
            self._pop(hub.NAME.upper(), text[:110] + ("…" if len(text) > 110 else ""), keep=7000)
        elif kind == "ask":
            self._pop("ICE DETECTADO", "aguardando autorização: " + args[1], keep=15000, color=QColor(255, 45, 75))
        elif kind == "look":
            self._pop("VARREDURA ÓPTICA", "kiroshi ativo: espiando sua tela...", keep=5000)
        elif kind == "error" or (kind == "answer" and not args[0]):
            self.stamp.fire(QColor(255, 45, 75), force=True, note="!erro",
                            detail=args[1] if kind == "error" else "acesso negado pelo usuário")
        elif kind == "done":
            self.hide_timer.start(4000)

    def _pop(self, title, line, keep, color=None):
        try:
            new = color or (_neon(self.accent()) if self.accent else None) or self.color
        except Exception:
            new = self.color
        if new != self.color:
            self._set_color(new)
        self.title, self.line, self.shown_chars = title, line, 0
        self.glitch = 10
        self.target = 1.0
        if color is not None:
            sfx.play("alarm")
        elif not self.isVisible() or self.phase == "close":
            sfx.play("open")
        if not self.isVisible() or self.phase == "close":
            self.phase, self.pt = "open", 0
        if not self.isVisible():
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.move(area.left() + 16, area.top() + 16)
            self.show()
            _hide_from_capture(self)
        self._raise()
        self.tick.start()
        self.hide_timer.start(keep)

    def _raise(self):
        plat.keep_on_top(self)  # other always-on-top windows (and games) can push us behind them

    def _fade_out(self):
        if self.busy_since and time.time() - self.busy_since < 180:
            self.hide_timer.start(3000)  # long job still running: stay on the line
            return
        self.target = 0.0
        if self.isVisible() and self.phase != "close":
            self.phase, self.pt = "close", 0
            sfx.play("close")

    def _step(self):
        self.t += 1
        self.pt += 1
        if self.phase == "open" and self.pt > 16:
            self.phase = "idle"
        if self.phase == "close" and self.pt > 14:
            self.tick.stop()
            self.hide()
            self.opacity = 0.0
            return
        try:
            loud = min(1.0, (self.level() if self.level else 0.0) * 5)
        except Exception:
            loud = 0.0
        self.voice += (loud - self.voice) * (0.6 if loud > self.voice else 0.25)
        for i in range(len(self.bars)):  # equalizer: each bar jitters around the voice level
            want = self.voice * (0.45 + 0.55 * abs(math.sin(i * 1.7 + self.t * 0.9))) * random.uniform(0.7, 1.15)
            self.bars[i] += (want - self.bars[i]) * 0.5
        if self.voice > 0.05 and self.target:
            self.hide_timer.start(4000)  # stay up while she is talking
        if self.t % 10 == 0:
            self._raise()
        self.opacity += (self.target - self.opacity) * 0.18
        self.shown_chars = min(len(self.line), self.shown_chars + 2)
        if self.glitch:
            self.glitch -= 1
        elif random.random() < 0.012:
            self.glitch = 3
        self.update()

    def _squash(self):
        """(sx, sy) of the CRT-style open/close: a neon line stretches out, then unfolds (and back)."""
        ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
        if self.phase == "open":
            f = self.pt / 16
            return (ease(f / 0.4), 0.0) if f < 0.4 else (1.0, ease((f - 0.4) / 0.6))
        if self.phase == "close":
            f = self.pt / 14
            return (1.0, 1 - ease(f / 0.5)) if f < 0.5 else (1 - ease((f - 0.5) / 0.5), 0.0)
        return 1.0, 1.0

    # --- drawing -------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        col = self.color
        sx, sy = self._squash()
        if sy < 0.06:  # just the bright scanline of a tube turning on/off
            lw = (W - 2 * PAD) * sx
            g = QLinearGradient(W / 2 - lw / 2, 0, W / 2 + lw / 2, 0)
            g.setColorAt(0, _alpha(col, 0)); g.setColorAt(0.5, QColor(255, 255, 255)); g.setColorAt(1, _alpha(col, 0))
            p.fillRect(QRectF(W / 2 - lw / 2, H / 2 - 4, lw, 8), _alpha(col, 60))
            p.fillRect(QRectF(W / 2 - lw / 2, H / 2 - 1, lw, 2), g)
            if self.phase == "open":
                p.setFont(self.font_small)
                p.setPen(col)
                p.drawText(QRectF(0, H / 2 + 6, W, 14), Qt.AlignHCenter,
                           "ESTABELECENDO LINK" + "." * (self.pt % 4))
            p.end()
            return
        p.translate(W / 2, H / 2)
        p.scale(sx, sy)
        p.translate(-W / 2, -H / 2)
        p.setOpacity(1.0 if self.phase != "idle" else self.opacity)
        jx = random.choice((-5, -3, 3, 5)) if self.glitch > 2 else 0

        l, t, r, b = PAD, PAD, W - PAD, H - PAD
        frame = QPainterPath()
        frame.moveTo(l + 18, t); frame.lineTo(r, t); frame.lineTo(r, b - 22)
        frame.lineTo(r - 22, b); frame.lineTo(l, b); frame.lineTo(l, t + 18); frame.closeSubpath()

        # body: dark glass with a whisper of the neon color
        bg = QLinearGradient(l, t, r, b)
        bg.setColorAt(0, QColor(6, 8, 14, 235))
        bg.setColorAt(1, _alpha(col.darker(600), 235))
        p.fillPath(frame, bg)
        p.save()
        p.setClipPath(frame, Qt.IntersectClip)
        hatch = QPen(_alpha(col, 14), 1)
        p.setPen(hatch)
        for x in range(int(r) - 140, int(r) + 60, 7):  # diagonal hatching bottom-right
            p.drawLine(QPointF(x, b), QPointF(x + 40, b - 40))
        p.restore()

        # neon glow frame
        boost = self.voice
        for width, a in ((7 + 6 * boost, 25 + 50 * boost), (3.5, 60 + 80 * boost), (1.3, 255)):
            p.setPen(QPen(_alpha(col, int(min(255, a))), width))
            p.drawPath(frame)

        # header strip
        hp = QPainterPath()
        hp.addRect(QRectF(l, t, r - l, 18))
        p.fillPath(hp.intersected(frame), col)
        p.setFont(self.font_head)
        p.setPen(QColor(8, 8, 12))
        if self.sigil_img:
            p.drawImage(QPointF(l + 22, t + 1), self.sigil_img)
        p.drawText(QRectF(l + 44, t, 200, 18), Qt.AlignVCenter, "HOLOCALL  //  NET.LINK")
        p.drawText(QRectF(r - 160, t, 150, 18), Qt.AlignVCenter | Qt.AlignRight,
                   "● REC" if self.t // 15 % 2 else "  REC")
        for i in range(5):  # little stripes in the header
            p.fillRect(QRectF(r - 175 + i * 5, t + 5, 2, 8), QColor(8, 8, 12))

        # hologram portrait
        box = QRectF(l + 12, t + 28, PORTRAIT, PORTRAIT - 12)
        p.save()
        p.setClipRect(box, Qt.IntersectClip)
        p.fillRect(box, _alpha(col, 22))
        if self.holo:
            x, y = box.x() + jx, box.y() - 4
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.setOpacity(self.opacity * (0.35 if self.glitch else 0.18))
            p.drawImage(QPointF(x - 2, y), self.holo_red)  # chromatic fringe
            p.setOpacity(self.opacity * min(1.0, 0.7 + 0.15 * math.sin(self.t * 0.5) + 0.5 * self.voice))
            p.drawImage(QPointF(x, y), self.holo)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setOpacity(self.opacity)
        for yy in range(int(box.y()) + self.t % 3, int(box.bottom()), 3):
            p.fillRect(QRectF(box.x(), yy, box.width(), 1), QColor(0, 0, 0, 80))
        band = box.y() + (self.t * 2.5) % (box.height() + 30) - 15
        sweep = QLinearGradient(0, band - 12, 0, band + 12)
        sweep.setColorAt(0, _alpha(col, 0)); sweep.setColorAt(0.5, _alpha(col, 70)); sweep.setColorAt(1, _alpha(col, 0))
        p.fillRect(QRectF(box.x(), band - 12, box.width(), 24), sweep)
        if self.glitch:  # torn slice
            sy = box.y() + random.random() * box.height()
            p.fillRect(QRectF(box.x(), sy, box.width(), 4), _alpha(col, 120))
        p.restore()
        # corner brackets around the portrait
        p.setPen(QPen(col, 2))
        k = 10
        for cx, cy, dx, dy in ((box.left(), box.top(), 1, 1), (box.right(), box.top(), -1, 1),
                               (box.left(), box.bottom(), 1, -1), (box.right(), box.bottom(), -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + k * dx, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + k * dy))

        # title, divider and message
        tx = box.right() + 16
        title = self.title
        if self.glitch:
            title = "".join(random.choice(GLYPHS) if random.random() < 0.3 else ch for ch in title)
        p.setFont(self.font_title)
        p.setPen(_alpha(col, 70))
        p.drawText(QRectF(tx + jx + 1, t + 27, r - tx - 12, 24), Qt.AlignVCenter, title)
        p.setPen(col)
        p.drawText(QRectF(tx + jx, t + 26, r - tx - 12, 24), Qt.AlignVCenter, title)
        line = QLinearGradient(tx, 0, r - 12, 0)
        line.setColorAt(0, col); line.setColorAt(1, _alpha(col, 0))
        p.fillRect(QRectF(tx, t + 53, r - tx - 12, 1.5), line)

        p.setFont(self.font_mono)
        p.setPen(QColor(225, 235, 240))
        shown = self.line[:self.shown_chars]
        if self.shown_chars < len(self.line):
            shown += random.choice(GLYPHS)
        elif self.t // 10 % 2:
            shown += "▌"
        p.drawText(QRectF(tx, t + 60, r - tx - 14, b - t - 92), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, shown)

        busy = time.time() - self.busy_since if self.busy_since else 0
        if busy > 2.5:
            self._paint_busy(p, box, tx, r, b, busy)
            p.end()
            return

        # footer: signal bars and trace info
        p.setPen(Qt.NoPen)
        for i in range(14):
            idle = 2 + abs(math.sin(self.t * 0.12 + i * 0.7)) * 3
            hgt = idle + self.bars[i] * 22
            bar = QColor(255, 255, 255) if self.bars[i] > 0.75 else col
            p.fillRect(QRectF(tx + i * 5, b - 12 - hgt, 3, hgt), bar if self.voice > 0.05 or i < 10 else _alpha(col, 50))
        p.setFont(self.font_small)
        p.setPen(_alpha(col, 170))
        p.drawText(QRectF(tx + 80, b - 26, r - tx - 110, 14), Qt.AlignRight | Qt.AlignVCenter,
                   f"ENCRYPTED · LINK {96 + self.t // 7 % 4}%")
        p.end()

    def _paint_busy(self, p, box, tx, r, b, busy):
        """Long job: spinning segmented ring over the portrait and an endless transfer bar."""
        col = self.color
        c = box.center()
        p.save()
        p.translate(c)
        p.setBrush(Qt.NoBrush)
        for radius, speed, seg, width in ((40, 6, 8, 3), (48, -3.5, 3, 1.5)):
            p.save()
            p.rotate(self.t * speed)
            span = 360 // seg
            for s in range(seg):
                lit = (s + self.t // 3) % seg == 0
                p.setPen(QPen(QColor(255, 255, 255) if lit else _alpha(col, 200), width))
                p.drawArc(QRectF(-radius, -radius, radius * 2, radius * 2), s * span * 16, int(span * 0.6) * 16)
            p.restore()
        p.restore()
        # marquee bar: a chunk of light sliding forever, with blocks behind it
        bar = QRectF(tx, b - 34, r - tx - 14, 7)
        p.setPen(QPen(_alpha(col, 140), 1))
        p.drawRect(bar)
        n = 24
        head = (self.t * 0.6) % (n + 6) - 3
        for i in range(n):
            d = abs(i - head)
            a = 255 if d < 1 else 150 if d < 2.5 else 35
            p.fillRect(QRectF(bar.x() + 2 + i * (bar.width() - 4) / n, bar.y() + 2, (bar.width() - 4) / n - 2, 3),
                       _alpha(col, a))
        p.setFont(self.font_small)
        p.setPen(col)
        dots = "." * (self.t // 8 % 4)
        p.drawText(QRectF(tx, b - 24, 200, 14), Qt.AlignLeft | Qt.AlignVCenter, "PROCESSANDO" + dots)
        p.drawText(QRectF(tx, b - 24, r - tx - 14, 14), Qt.AlignRight | Qt.AlignVCenter,
                   "T+%02d:%02d  ·  %s" % (int(busy) // 60, int(busy) % 60, random.choice(("RX", "TX", "IO", "MEM"))))


_QUIPS = (
    (("!erro",), ("ops. finge que não viu", "não fui eu, foi o Windows", "erro técnico, nada pessoal")),
    (("apag", "remov", "delet", "limp"), ("faxina digital, nada pessoal", "sumindo com o lixo, de nada", "isso aqui ninguém vai sentir falta")),
    (("print", "captur", "screenshot"), ("sorria, você tá sendo vigiado", "registrado pra posteridade", "clique! saiu com o olho fechado")),
    (("fech", "stop-process", "encerr", "matar", "finaliz"), ("tchauzinho, programa", "desligado com sucesso e zero remorso", "esse aí já era")),
    (("abri", "start-process", "inici", "lança"), ("abrindo pra você, vossa preguiça", "deixa que eu clico, né", "pronto, aberto. aplausos")),
    (("procur", "busca", "pesquis", "achar", "localiz"), ("fuçando onde não fui chamada", "procurando... sem julgar suas pastas. muito", "modo detetive ligado")),
    (("ler ", "olhar", "verific", "checar", "ver "), ("só dando uma espiadinha", "lendo isso pra você não precisar", "hmm, interessante... ou não")),
    (("edit", "alter", "mud", "troc", "corrig", "ajust"), ("mexendo no código, segura minha mão", "ajustinho de leve, confia", "consertando o que alguém quebrou")),
    (("baix", "instal", "download"), ("baixando coisa, cruza os dedos", "instalando com toda a minha paciência")),
)
_GENERIC = ("invadindo com carinho", "só uma hackeadinha básica", "relaxa, eu sei o que tô fazendo. acho",
            "trabalhando enquanto você só olha", "netrunner em serviço, sem hora extra", "mais um sistema pra coleção")


def _quip(note):
    low = (note or "").lower()
    for keys, lines in _QUIPS:
        if any(k in low for k in keys):
            return "> " + random.choice(lines)
    return "> " + random.choice(_GENERIC)


class SigilStamp(QWidget):
    """Yui's netrunner mark, flashed over the window she is messing with.

    Covers the whole target window: brackets lock onto its corners, a scan sweeps it, hex
    dumps scroll on the sides, a breach bar fills and then the mark glitches in.
    """
    SIZE = 220
    LIFE = 3.8
    CALM = 180  # seconds of light tags after a full breach

    def __init__(self, sigil_path):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.mask = _sigil(sigil_path, self.SIZE)
        self.img = self.img_red = None
        self.color = FALLBACK
        self.start, self.last, self.last_full = 0.0, 0.0, -1e9
        self.lite, self.life, self.quip = False, self.LIFE, ""
        self.mode, self.blocks, self.detail = "breach", [], ""
        self.snap, self.tears = None, []
        self.addr = "0x0000"
        self.hex = []
        self.tick = QTimer(self, interval=33, timeout=self._step)

    def set_sigil(self, path):
        self.mask = _sigil(path, self.SIZE)
        self.img = None

    def fire(self, color, force=False, note="", detail=""):
        if not self.mask or (not force and time.time() - self.last < 6):
            return  # one tag per hack, not one per command
        if not force and self._own_focus():
            return  # never tag Yui's own app
        if note == "!erro" and time.time() - self.start < self.life:
            return  # don't cut an effect that is still playing
        self.last = self.start = time.time()
        low = (note or "").lower()
        self.detail = detail
        self.query = note.split(":", 1)[1].strip() if ":" in (note or "") else (note or "")
        # deleting, launching etc. get their own effect; anything else is a breach
        self.mode = ("error" if note == "!erro"
                     else "search" if any(k in low for k in ("pesquis", "procur", "busca", "googl"))
                     else "download" if any(k in low for k in ("baix", "download", "instal", "winget", "pip "))
                     else "purge" if any(k in low for k in ("apag", "remov", "delet", "limp", "remove-item"))
                     else "snap" if any(k in low for k in ("print", "captur", "screenshot", "tirar foto"))
                     else "kill" if any(k in low for k in ("fech", "stop-process", "encerr", "matar", "kill", "finaliz"))
                     else "launch" if any(k in low for k in ("abri", "start-process", "inici", "lança"))
                     else "breach")
        # first tag is the full breach; for a while after that only a quiet light tag
        self.lite = self.mode == "breach" and not force and self.start - self.last_full < self.CALM
        if self.mode == "breach" and not self.lite:
            self.last_full = self.start
        self.life = {"purge": 2.8, "launch": 2.5, "kill": 2.9, "snap": 2.2, "search": 2.4,
                                "download": 2.6, "error": 2.6}.get(self.mode, 1.8 if self.lite else self.LIFE)
        if self.mode == "purge":  # the window crumbles in random blocks
            self.blocks = [(random.uniform(0.25, 1.4), random.uniform(-1, 1)) for _ in range(400)]
        self.quip = _quip(note)
        if color != self.color or self.img is None:
            self.color = color
            self.img = _tint(self.mask, color)
            self.img_red = _tint(self.mask, QColor(255, 40, 90))
            self.img_dark = _tint(self.mask, QColor(8, 8, 12))
        self.addr = "0x%04X:%04X" % (random.randint(0, 0xFFFF), random.randint(0, 0xFFFF))
        self.hex = [" ".join("%02X" % random.randint(0, 255) for _ in range(4)) for _ in range(60)]
        self._place(window=self.mode in ("breach", "kill"))
        self.tears = []
        self.show()
        _hide_from_capture(self)
        plat.keep_on_top(self)
        self.tick.start()
        sfx.play(self.mode if self.mode != "breach" else "soft" if self.lite else "hack")

    @staticmethod
    def _own_focus():
        return plat.own_window_focused()

    def _place(self, window=True):
        # cover the window in focus (physical pixels -> Qt's logical ones); with no real window
        # (desktop, taskbar, nothing focused) or a screen-wide effect, take the whole screen the
        # focus is on. A snapshot of what's underneath is kept to glitch the real pixels.
        side = int(self.SIZE * 1.6)
        screen = QGuiApplication.primaryScreen()
        rect = QRectF(screen.geometry())
        fg = plat.foreground()
        self.target, self.target_rect, self.frames = None, None, 0
        if fg and not fg.desktop and fg.rect and fg.rect[2] - fg.rect[0] > 300:
            left, top, right, bottom = fg.rect
            if window:  # remember it so the effect can follow the window while it plays
                self.target, self.target_rect = fg.id, fg.rect
            for s in QGuiApplication.screens():
                g, k = s.geometry(), s.devicePixelRatio()
                cx, cy = (left + right) / 2, (top + bottom) / 2
                if g.x() * k <= cx < (g.x() + g.width()) * k and g.y() * k <= cy < (g.y() + g.height()) * k:
                    screen = s
                    if window:
                        win = QRectF(left / k, top / k, (right - left) / k, (bottom - top) / k)
                        rect = win.intersected(QRectF(g)).adjusted(8, 8, -8, -8)
                    else:
                        rect = QRectF(g)
                    break
        if rect.width() < side or rect.height() < side + 40:
            c = rect.center()
            rect = QRectF(c.x() - max(side, rect.width()) / 2, c.y() - max(side + 40, rect.height()) / 2,
                          max(side, rect.width()), max(side + 40, rect.height()))
        g = screen.geometry()
        self.snap = screen.grabWindow(0, int(rect.x() - g.x()), int(rect.y() - g.y()), int(rect.width()), int(rect.height()))
        self.setGeometry(rect.toRect())

    def _step(self):
        if time.time() - self.start > self.life:
            self.tick.stop()
            self.hide()
            return
        self._follow()
        self.update()

    def _follow(self):
        """Keep the effect glued to its window: moved or resized it tracks, closed or minimized it ends."""
        self.frames += 1
        if not self.target or (not plat.WINDOWS and self.frames % 4):  # Linux asks xdotool: not every frame
            return
        now = plat.window_rect(self.target)
        if now is None:
            self.target = None
            self.start = min(self.start, time.time() - self.life + 0.3)  # fade out quickly
            return
        if now == self.target_rect:
            return
        old = self.target_rect
        self.target_rect = now
        k = self.screen().devicePixelRatio() if self.screen() else 1.0
        g = self.geometry()
        # shift/grow by the same delta the window did (physical -> logical pixels)
        self.setGeometry(int(g.x() + (now[0] - old[0]) / k), int(g.y() + (now[1] - old[1]) / k),
                         max(1, int(g.width() + ((now[2] - now[0]) - (old[2] - old[0])) / k)),
                         max(1, int(g.height() + ((now[3] - now[1]) - (old[3] - old[1])) / k)))

    def paintEvent(self, _):
        if not self.img:
            return
        age = time.time() - self.start
        fade = max(0.0, min(1.0, age / 0.12) * min(1.0, (self.life - age) / 0.6))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(fade)
        col = self.color
        w, h = self.width(), self.height()
        # every effect opens with the real screen tearing apart, error keeps tearing for longer
        burst = 0.9 if self.mode == "error" else 0.3
        if age < burst:
            self._glitch(p, w, h, 1 - age / burst)
        elif random.random() < 0.04:
            self._glitch(p, w, h, 0.25)
        if self.mode not in ("breach", "kill"):
            self._hud(p, w, h, age, QColor(255, 45, 75) if self.mode in ("error", "purge") else col)
        if self.mode == "purge":
            self._paint_purge(p, age, fade, w, h)
            return
        if self.mode == "launch":
            self._paint_launch(p, age, fade, w, h)
            return
        if self.mode == "kill":
            self._paint_kill(p, age, fade, w, h)
            return
        if self.mode == "snap":
            self._paint_snap(p, age, fade, w, h)
            return
        paint = {"search": self._paint_search, "download": self._paint_download, "error": self._paint_error}.get(self.mode)
        if paint:
            paint(p, age, fade, w, h)
            return

        if self.lite:
            self._paint_lite(p, age, fade, w / 2, h / 2 - 20)
            return
        self._paint_breach(p, age, fade, w, h)

    # --- 2077 kit ---------------------------------------------------------------
    def _glitch(self, p, w, h, amount):
        """Tear the real pixels under the effect: displaced slices with a red/cyan split."""
        if not self.snap or self.snap.isNull() or amount <= 0:
            return
        sw, sh = self.snap.width(), self.snap.height()
        kx, ky = sw / max(1, w), sh / max(1, h)
        p.save()
        for _ in range(int(3 + 9 * amount)):
            y, bh = random.uniform(0, h), random.uniform(4, 10 + 50 * amount)
            dx = random.uniform(-1, 1) * 60 * amount
            src = QRectF(0, y * ky, sw, bh * ky)
            p.setOpacity(1.0)
            p.drawPixmap(QRectF(dx, y, w, bh), self.snap, src)
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.fillRect(QRectF(dx - 6, y, 6, bh), QColor(255, 30, 60, 120))
            p.fillRect(QRectF(dx + w, y, 6, bh), QColor(0, 240, 255, 120))
            p.setOpacity(0.35 * amount)
            p.drawPixmap(QRectF(dx + 8, y, w, bh), self.snap, src)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            if random.random() < 0.3:  # dead data block
                p.setOpacity(0.8)
                p.fillRect(QRectF(random.uniform(0, w), y, random.uniform(20, 160), bh),
                           random.choice((QColor(255, 30, 60), QColor(0, 240, 255), YELLOW, QColor(8, 8, 12))))
        p.restore()

    def _hud(self, p, w, h, age, col):
        """Screen-wide netrunner HUD: framing lines with ticks, side readouts, barcode and serial."""
        p.save()
        p.setOpacity(p.opacity() * min(1.0, age / 0.2))
        # vignette + scanlines so the effect reads as "inside the net"
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 4, 10, 70))
        for y in range(int(age * 40) % 4, h, 4):
            p.fillRect(QRectF(0, y, w, 1), QColor(0, 0, 0, 40))
        p.setPen(QPen(_alpha(col, 150), 1))
        m = 24
        p.drawLine(QPointF(m, m + 30), QPointF(m, h - m - 30))
        p.drawLine(QPointF(w - m, m + 30), QPointF(w - m, h - m - 30))
        for y in range(m + 40, h - m - 40, 24):
            p.drawLine(QPointF(m, y), QPointF(m + (10 if y % 120 == 0 else 5), y))
            p.drawLine(QPointF(w - m, y), QPointF(w - m - (10 if y % 120 == 0 else 5), y))
        # top bar: cut-corner tab with the title, thin line running off it
        tab = QPainterPath()
        tab.moveTo(m, m); tab.lineTo(m + 300, m); tab.lineTo(m + 316, m + 16); tab.lineTo(m, m + 16); tab.closeSubpath()
        p.fillPath(tab, col)
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(QColor(8, 8, 12))
        p.drawText(QRectF(m + 8, m, 300, 16), Qt.AlignVCenter, hub.NAME.upper() + "//NET.ARCH  ::  " + self.mode.upper() + "  ::  " + self.addr)
        p.setPen(QPen(_alpha(col, 160), 1))
        p.drawLine(QPointF(m + 316, m + 16), QPointF(w - m, m + 16))
        p.fillRect(QRectF(w - m - 60, m + 12, 60, 4), YELLOW)
        # Yui's mark always signs the screen, top-right, with a chromatic fringe and a flicker
        s = 72
        mx, my = w - m - s - 8, m + 28
        p.setPen(QPen(_alpha(col, 160), 1))
        p.setBrush(QColor(6, 8, 14, 200))
        p.drawRect(QRectF(mx - 6, my - 6, s + 12, s + 12))
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.drawImage(QRectF(mx - 3, my, s, s), self.img_red)
        if random.random() > 0.05:
            p.drawImage(QRectF(mx, my, s, s), self.img)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setFont(QFont("Consolas", 7, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(mx - 6, my + s + 8, s + 12, 12), Qt.AlignHCenter | Qt.AlignVCenter, "NETRUNNER: " + hub.NAME.upper())
        # barcode + serial bottom-left, live counters bottom-right
        random.seed(hash(self.addr))
        x = m
        for _ in range(40):
            bw = random.choice((1, 1, 2, 3))
            p.fillRect(QRectF(x, h - m - 22, bw, 14), _alpha(col, 180))
            x += bw + random.choice((1, 2))
        random.seed()
        p.setFont(QFont("Consolas", 7))
        p.setPen(_alpha(col, 200))
        p.drawText(QRectF(m, h - m - 6, 300, 12), Qt.AlignLeft | Qt.AlignVCenter, "SN 2077-%s-%s  ·  KIROSHI LINK OK" % (hub.NAME.upper()[:6], self.addr[2:6]))
        p.drawText(QRectF(w - m - 300, h - m - 20, 300, 12), Qt.AlignRight | Qt.AlignVCenter,
                   "RAM %d/12  ·  TRACE %d%%  ·  %.2fs" % (3 + int(age * 3) % 9, min(99, int(age * 17)), age))
        p.fillRect(QRectF(w - m - 120, h - m - 4, 120 * min(1.0, age / self.life), 2), YELLOW)
        p.restore()

    def _paint_breach(self, p, age, fade, w, h):
        """Breach Protocol: lock onto the window, solve the code matrix, upload daemons, ACCESS GRANTED."""
        col = self.color
        cx, cy = w / 2, h / 2
        ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
        # target lock brackets + quickhack tab on the window
        lock = ease(age / 0.3)
        inset = 70 * (1 - lock)
        p.setPen(QPen(YELLOW, 3))
        for x0, y0, dx, dy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            x, y = x0 - dx * inset + dx * 2, y0 - dy * inset + dy * 2
            p.drawLine(QPointF(x, y), QPointF(x + 40 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 40 * dy))
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 4, 10, int(130 * lock)))
        for y in range(int(age * 40) % 4, h, 4):
            p.fillRect(QRectF(0, y, w, 1), QColor(0, 0, 0, 40))

        # the panel: code matrix on the left, buffer + daemons on the right
        pw, ph = 640, 300
        open_k = ease((age - 0.2) / 0.25)
        if open_k <= 0:
            p.end()
            return
        # laid out at a fixed size, scaled down to fit narrow windows
        k = min(1.0, (w - 60) / (pw + 40), (h - 120) / ph)
        p.translate(cx, cy)
        p.scale(k, k)
        p.translate(-cx, -cy)
        box = QRectF(cx - pw / 2, cy - ph / 2 * open_k, pw, ph * open_k)
        cut = QPainterPath()
        cut.moveTo(box.left(), box.top()); cut.lineTo(box.right() - 20, box.top()); cut.lineTo(box.right(), box.top() + 20)
        cut.lineTo(box.right(), box.bottom()); cut.lineTo(box.left() + 20, box.bottom()); cut.lineTo(box.left(), box.bottom() - 20)
        cut.closeSubpath()
        p.fillPath(cut, QColor(6, 8, 14, 235))
        p.setPen(QPen(col, 1.5))
        p.drawPath(cut)
        if open_k < 1:
            p.end()
            return
        bx, by = box.x(), box.y()
        p.setFont(QFont("Consolas", 10, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(bx + 18, by + 10, 300, 18), Qt.AlignVCenter, "BREACH PROTOCOL")
        timer = max(0.0, 2.0 - age)
        p.setPen(YELLOW)
        p.drawText(QRectF(bx, by + 10, pw - 18, 18), Qt.AlignRight | Qt.AlignVCenter, "TEMPO LIMITE  %05.2f" % timer)
        p.fillRect(QRectF(bx + 18, by + 32, (pw - 36) * timer / 2.0, 3), YELLOW)

        # code matrix: picks alternate row / column, like the game
        random.seed(hash(self.addr))
        codes = ("1C", "55", "BD", "E9", "7A", "FF")
        grid = [[random.choice(codes) for _ in range(5)] for _ in range(5)]
        path, r, c = [], 0, random.randrange(5)
        for i in range(5):
            if i % 2 == 0:
                c = random.choice([k for k in range(5) if (r, k) not in path] or [c])
            else:
                r = random.choice([k for k in range(5) if (k, c) not in path] or [r])
            path.append((r, c))
        random.seed()
        picks = max(0, min(5, int((age - 0.55) / 0.22)))
        cell = 38
        gx, gy = bx + 30, by + 56
        p.setPen(QPen(_alpha(col, 120), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(gx - 10, gy - 8, cell * 5 + 20, cell * 5 + 16))
        if picks < 5:  # highlight band: row on even steps, column on odd
            pr, pc = path[picks - 1] if picks else (0, path[0][1])
            band = QRectF(gx - 4, gy + pr * cell, cell * 5 + 8, cell) if picks % 2 == 0 else QRectF(gx + pc * cell, gy - 4, cell, cell * 5 + 8)
            p.fillRect(band, _alpha(col, 45))
        p.setFont(QFont("Consolas", 12, QFont.Bold))
        for rr in range(5):
            for cc in range(5):
                rect = QRectF(gx + cc * cell, gy + rr * cell, cell, cell)
                if (rr, cc) in path[:picks]:
                    p.setPen(QPen(YELLOW, 1.5))
                    p.drawRect(rect.adjusted(4, 4, -4, -4))
                    p.setPen(_alpha(YELLOW, 120))
                    p.drawText(rect, Qt.AlignCenter, "[ ]")
                else:
                    p.setPen(_alpha(col, 230 if random.random() > 0.03 else 90))
                    p.drawText(rect, Qt.AlignCenter, grid[rr][cc])

        # buffer slots and daemon list
        rx = gx + cell * 5 + 40
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(rx, gy - 8, 200, 14), Qt.AlignVCenter, "BUFFER")
        p.setFont(QFont("Consolas", 11, QFont.Bold))
        for i in range(6):
            slot = QRectF(rx + i * 40, gy + 10, 34, 30)
            p.setPen(QPen(_alpha(col, 160), 1, Qt.DashLine if i >= picks else Qt.SolidLine))
            p.drawRect(slot)
            if i < picks:
                p.setPen(QColor(255, 255, 255) if i == picks - 1 else YELLOW)
                p.drawText(slot, Qt.AlignCenter, grid[path[i][0]][path[i][1]])
        daemons = (("ICEPICK", 2), ("DATAMINE_V3", 4), (hub.NAME.upper()[:8] + ".BACKDOOR", 5))
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(rx, gy + 56, 200, 14), Qt.AlignVCenter, "SEQUÊNCIAS NECESSÁRIAS")
        for i, (name, need) in enumerate(daemons):
            y = gy + 76 + i * 34
            ok = picks >= need
            row = QRectF(rx, y, pw - (rx - bx) - 24, 28)
            p.fillRect(row, _alpha(QColor(60, 255, 140) if ok else col, 55 if ok else 18))
            p.setPen(QPen(QColor(60, 255, 140) if ok else _alpha(col, 120), 1))
            p.drawRect(row)
            p.setFont(QFont("Consolas", 9, QFont.Bold))
            p.setPen(QColor(60, 255, 140) if ok else QColor(220, 230, 240))
            p.drawText(row.adjusted(10, 0, -10, 0), Qt.AlignLeft | Qt.AlignVCenter, name)
            p.drawText(row.adjusted(10, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, "INSTALADO" if ok else "· " * need)

        # result: banner slams across the panel, the mark stamps over it
        if picks >= 5:
            t = age - (0.55 + 5 * 0.22)
            if t < 0.12:
                p.fillRect(QRectF(0, 0, w, h), _alpha(YELLOW, int(90 * (1 - t / 0.12))))
            bh = 54 * ease(t / 0.15)
            banner = QRectF(bx - 20, cy - bh / 2, pw + 40, bh)
            p.fillRect(banner, YELLOW)
            p.setFont(QFont("Consolas", 22, QFont.Bold))
            text = "ACESSO CONCEDIDO"
            if t < 0.3:
                text = "".join(random.choice(GLYPHS) if random.random() < 0.3 else ch for ch in text)
            p.setPen(QColor(8, 8, 12))
            p.drawText(banner.adjusted(90, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
            s = 70 * (1.4 - 0.4 * ease(t / 0.2))
            p.drawImage(QRectF(banner.x() + 46 - s / 2, cy - s / 2, s, s), self.img_dark)
            bottom = cy + (box.bottom() - cy) * k
            p.resetTransform()
            self._paint_quip(p, t - 0.25, bottom + 14)
        p.end()

    def _paint_lite(self, p, age, fade, cx, cy):
        # quiet tag: small mark eases in with a single slow ring, plus Yui's comment
        s = self.SIZE * 0.5 * (0.9 + 0.1 * min(1.0, age / 0.3))
        p.save()
        p.translate(cx, cy)
        p.rotate(age * 40)
        p.setPen(QPen(_alpha(self.color, 110), 1.5))
        p.setBrush(Qt.NoBrush)
        r = s * 0.72
        for k in range(3):
            p.drawArc(QRectF(-r, -r, r * 2, r * 2), k * 120 * 16, 80 * 16)
        p.restore()
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(fade * 0.85)
        p.drawImage(QRectF(cx - s / 2, cy - s / 2, s, s), self.img)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(fade)
        self._paint_quip(p, age - 0.2, cy + s * 0.75 + 6)
        p.end()

    def _paint_purge(self, p, age, fade, w, h):
        # deletion: red alert, the window shatters into blocks that flash and fall away
        red = QColor(255, 45, 75)
        cell = max(36, int(max(w, h) / 20))
        cols, rows = w // cell + 1, h // cell + 1
        for i in range(min(cols * rows, len(self.blocks))):
            delay, drift = self.blocks[i]
            x, y = (i % cols) * cell, (i // cols) * cell
            k = (age - delay) / 0.5
            if k < 0:
                p.fillRect(QRectF(x + 1, y + 1, cell - 2, cell - 2), _alpha(red, 22))
            elif k < 1:
                fall = k * k * cell * 2.5
                p.fillRect(QRectF(x + drift * fall * 0.4 + 1, y + fall + 1, cell - 2, cell - 2),
                           _alpha(QColor(255, 255, 255) if k < 0.15 else red, int(150 * (1 - k))))
        p.setPen(QPen(red, 3))
        for cx, cy, dx, dy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            p.drawLine(QPointF(cx + dx * 2, cy + dy * 2), QPointF(cx + dx * 36, cy + dy * 2))
            p.drawLine(QPointF(cx + dx * 2, cy + dy * 2), QPointF(cx + dx * 2, cy + dy * 36))
        # warning stripes on top and bottom
        off = (age * 80) % 28
        for yy in (0, h - 14):
            p.save()
            p.setClipRect(QRectF(0, yy, w, 14))
            for x in range(-28, w + 28, 28):
                path = QPainterPath()
                path.moveTo(x + off, yy + 14); path.lineTo(x + off + 14, yy); path.lineTo(x + off + 28, yy)
                path.lineTo(x + off + 14, yy + 14); path.closeSubpath()
                p.fillPath(path, _alpha(red, 150))
            p.restore()
        # counter and caption
        cx, cy = w / 2, h / 2
        prog = min(1.0, age / 1.6)
        p.setFont(QFont("Consolas", 22, QFont.Bold))
        p.setPen(QColor(255, 255, 255) if int(age * 8) % 2 else red)
        text = "DADOS ELIMINADOS" if prog >= 1 else "PURGE %d%%" % (prog * 100)
        if prog < 1 and random.random() < 0.3:
            text = "".join(random.choice(GLYPHS) if random.random() < 0.2 else ch for ch in text)
        p.drawText(QRectF(0, cy - 40, w, 40), Qt.AlignHCenter | Qt.AlignVCenter, text)
        p.setFont(QFont("Consolas", 9))
        p.setPen(red)
        left = int(0x3FFFFF * (1 - prog))
        p.drawText(QRectF(0, cy + 2, w, 18), Qt.AlignHCenter | Qt.AlignVCenter,
                   "// apagando setores  0x%06X  restantes" % left)
        if self.img_red and self.SIZE < h - 100:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.setOpacity(fade * 0.25)
            s = self.SIZE * 0.6
            p.drawImage(QRectF(cx - s / 2 + random.uniform(-4, 4), cy - s - 50, s, s), self.img_red)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setOpacity(fade)
        self._paint_quip(p, age - 1.0, cy + 30)
        p.end()

    def _paint_launch(self, p, age, fade, w, h):
        # launching: a boot terminal unfolds in the middle, types a fast load log next to the mark,
        # fills a bar and then bursts into a bright "EXECUTADO"
        col = self.color
        cx, cy = w / 2, h / 2
        bw, bh = min(w - 40, 560), 190
        open_k = 1 - (1 - min(1.0, age / 0.2)) ** 3
        box = QRectF(cx - bw / 2, cy - bh / 2 * open_k, bw, max(2, bh * open_k))
        p.setPen(Qt.NoPen)
        p.fillRect(box, QColor(4, 6, 12, 225))
        if age < 0.2:
            p.end()
            return
        # header strip and frame with cut corner
        p.fillRect(QRectF(box.x(), box.y(), bw, 18), col)
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(QColor(8, 8, 12))
        p.drawText(QRectF(box.x() + 10, box.y(), bw - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, hub.NAME.upper()[:8] + ".EXE  //  LAUNCHER")
        p.drawText(QRectF(box.x() + 10, box.y(), bw - 20, 18), Qt.AlignRight | Qt.AlignVCenter, self.addr)
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        # the mark on the left, glitching in
        s = 120
        mx, my = box.x() + 18, box.y() + 34
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        jit = random.uniform(-6, 6) if age < 0.5 else 0
        p.setOpacity(fade * 0.4)
        p.drawImage(QRectF(mx - 3 + jit, my, s, s), self.img_red)
        p.setOpacity(fade)
        p.drawImage(QRectF(mx + jit, my, s, s), self.img)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        # boot log typing fast
        log = ("> handshake ............ OK", "> alocando memória ..... OK", "> injetando processo ... OK",
               "> desviando do ICE ..... OK", "> executando")
        p.setFont(QFont("Consolas", 9))
        tx = mx + s + 20
        for i, ln in enumerate(log):
            k = (age - 0.25 - i * 0.14) / 0.12
            if k <= 0:
                break
            txt = ln[:int(len(ln) * min(1.0, k))]
            p.setPen(QColor(120, 255, 160) if txt.endswith("OK") else QColor(220, 230, 240))
            p.drawText(QRectF(tx, my + i * 17, box.right() - tx - 12, 17), Qt.AlignLeft | Qt.AlignVCenter, txt)
        # progress bar, then the burst
        prog = min(1.0, max(0.0, (age - 0.4) / 0.6))
        bar = QRectF(tx, box.bottom() - 34, box.right() - tx - 16, 10)
        p.setPen(QPen(_alpha(col, 160), 1))
        p.drawRect(bar)
        p.fillRect(QRectF(bar.x() + 2, bar.y() + 2, (bar.width() - 4) * prog, 6), col)
        done = prog >= 1
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(QColor(255, 255, 255) if done else col)
        p.drawText(QRectF(tx, bar.bottom() + 2, bar.width(), 14), Qt.AlignLeft | Qt.AlignVCenter,
                   ">> EXECUTADO" if done else "CARREGANDO %d%%" % (prog * 100))
        if done and age < 1.25:  # burst: the box flashes and ripples outward
            f = (age - 1.0) / 0.25
            p.fillRect(box, _alpha(col, int(90 * (1 - f))))
            g = 30 * f
            p.setPen(QPen(_alpha(col, int(200 * (1 - f))), 2))
            p.drawRect(box.adjusted(-g, -g, g, g))
        self._paint_quip(p, age - 1.0, box.bottom() + 14)
        p.end()

    def _paint_kill(self, p, age, fade, w, h):
        """Short Circuit quickhack: upload bar on the window, it fries with real-pixel glitches,
        then the window itself (its snapshot) shuts off like an old CRT with the mark burned in."""
        red = QColor(255, 45, 75)
        cx, cy = w / 2, h / 2
        ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
        up = min(1.0, age / 0.7)
        # 0.7-1.2s: the window fries; 1.2-1.7s: it collapses to a line, then a dot
        f = (age - 1.2) / 0.5
        sy = 1.0 if f < 0 else max(0.01, 1 - ease(f / 0.6))
        sx = 1.0 if f < 0.6 else max(0.0, 1 - ease((f - 0.6) / 0.4))
        if age > 0.7 and self.snap and not self.snap.isNull():
            p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, int(255 * min(1.0, (age - 0.7) / 0.1))))
            rw, rh = w * sx, h * sy
            if sx > 0:
                p.drawPixmap(QRectF(cx - rw / 2, cy - rh / 2, rw, rh), self.snap, QRectF(self.snap.rect()))
                p.fillRect(QRectF(cx - rw / 2, cy - rh / 2, rw, rh), _alpha(QColor(255, 255, 255), int(200 * (1 - sy))))
                if f < 0:
                    self._glitch(p, w, h, 0.5 + 0.5 * random.random())
                    p.fillRect(QRectF(0, 0, w, h), _alpha(red, int(40 * random.random())))
            elif age < 2.0:
                r = 7 * (1 - (age - 1.7) / 0.3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255))
                p.drawEllipse(QPointF(cx, cy), max(0, r), max(0, r))
        # quickhack upload tab, pinned top-left of the window like the game's
        if age < 1.2:
            tab = QRectF(16, 16, 330, 58)
            p.fillRect(tab, QColor(6, 8, 14, 230))
            p.setPen(QPen(YELLOW, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRect(tab)
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(YELLOW)
            p.drawText(QRectF(28, 22, 300, 14), Qt.AlignVCenter, "QUICKHACK  ·  CUSTO 4 RAM")
            p.setFont(QFont("Consolas", 13, QFont.Bold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(28, 36, 300, 18), Qt.AlignVCenter, "CURTO-CIRCUITO")
            p.fillRect(QRectF(28, 60, 306 * up, 5), YELLOW if up < 1 else red)
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.drawText(QRectF(28, 36, 306, 18), Qt.AlignRight | Qt.AlignVCenter,
                       "UPLOAD %d%%" % (up * 100) if up < 1 else "EXECUTADO")
        # the mark burned on the dying screen
        if age > 0.7 and sx > 0:
            s = self.SIZE * 0.7 * (1.3 - 0.3 * ease((age - 0.7) / 0.2))
            p.save()
            p.translate(cx, cy)
            p.scale(sx, sy)
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.drawImage(QRectF(-s / 2 - 5, -s / 2, s, s), self.img_red)
            p.drawImage(QRectF(-s / 2, -s / 2, s, s), self.img)
            p.restore()
            if sy > 0.5:
                p.setFont(QFont("Consolas", 16, QFont.Bold))
                p.setPen(red if int(age * 10) % 2 else QColor(255, 255, 255))
                p.drawText(QRectF(0, cy + s / 2 + 6, w, 30), Qt.AlignHCenter | Qt.AlignVCenter, "// PROCESSO ENCERRADO")
        self._paint_quip(p, age - 1.8, cy - 12)
        p.end()

    def _paint_snap(self, p, age, fade, w, h):
        # screenshot: optical scan, like a netrunner's kiroshi eyes: reticle locks, shutter flash,
        # then an analysis grid sweeps and tags a few "objects"
        col = self.color
        cx, cy = w / 2, h / 2
        ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
        # viewfinder: corner brackets zoom in from oversized to the frame
        z = 1 + 0.25 * (1 - ease(age / 0.3))
        k = 46
        p.setPen(QPen(col, 3))
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x, y = cx + sx * (w / 2 - 14) * z, cy + sy * (h / 2 - 14) * z
            p.drawLine(QPointF(x, y), QPointF(x - sx * k, y))
            p.drawLine(QPointF(x, y), QPointF(x, y - sy * k))
        # center reticle with the mark, rotating into place
        p.save()
        p.translate(cx, cy)
        p.rotate(90 * (1 - ease(age / 0.4)))
        r = 46
        p.setPen(QPen(_alpha(col, 220), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        for a in range(4):
            p.rotate(90)
            p.drawLine(QPointF(r - 10, 0), QPointF(r + 16, 0))
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(fade * 0.8)
        p.drawImage(QRectF(-28, -28, 56, 56), self.img)
        p.restore()
        p.setOpacity(fade)
        # shutter: two blades close and reopen, then a white flash
        if 0.3 < age < 0.55:
            f = (age - 0.3) / 0.25
            cover = (1 - abs(1 - 2 * f)) * h / 2
            p.fillRect(QRectF(0, 0, w, cover), QColor(4, 6, 12, 230))
            p.fillRect(QRectF(0, h - cover, w, cover), QColor(4, 6, 12, 230))
        if 0.45 < age < 0.8:
            p.fillRect(QRectF(0, 0, w, h), QColor(255, 255, 255, int(170 * (1 - (age - 0.45) / 0.35))))
        # analysis grid sweeping left to right
        if age > 0.6:
            sx = w * min(1.0, (age - 0.6) / 0.8)
            p.setPen(QPen(_alpha(col, 35), 1))
            for x in range(0, int(sx), 40):
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            for y in range(0, h, 40):
                p.drawLine(QPointF(0, y), QPointF(sx, y))
            p.fillRect(QRectF(sx - 2, 0, 2, h), _alpha(col, 220))
            # tags dropping on random spots behind the sweep
            random.seed(int(self.start))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            for i in range(5):
                tx, ty = random.uniform(0.1, 0.8) * w, random.uniform(0.15, 0.8) * h
                label = random.choice(("OBJ", "TXT", "ICON", "UI", "DATA")) + "_%02d  %d%%" % (i, random.randint(71, 99))
                if tx < sx:
                    p.setPen(QPen(col, 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(QRectF(tx, ty, 70, 44))
                    p.fillRect(QRectF(tx, ty - 14, 92, 14), col)
                    p.setPen(QColor(8, 8, 12))
                    p.drawText(QRectF(tx + 4, ty - 14, 90, 14), Qt.AlignVCenter, label)
            random.seed()
        # header readout
        p.setFont(QFont("Consolas", 10, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(60, 16, w - 120, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   "● CAPTURA ÓPTICA" if int(age * 4) % 2 else "  CAPTURA ÓPTICA")
        p.drawText(QRectF(60, 16, w - 120, 18), Qt.AlignRight | Qt.AlignVCenter, "%dx%d  ·  ZOOM 1.0x" % (w, h))
        self._paint_quip(p, age - 1.1, cy + 70)
        p.end()

    def _paint_search(self, p, age, fade, w, h):
        """Net trace: the mark sits in a hexagon at the center, fires packets down cables to nodes
        around it; each node hit pops a result tag, the query types in a bar on top."""
        col = self.color
        cx, cy = w / 2, h / 2
        ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
        R = min(w, h) * 0.32
        # query bar
        query = self.query or "dados"
        bar = QRectF(cx - 300, cy - R - 90, 600, 36)
        p.fillRect(bar, QColor(6, 8, 14, 230))
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar)
        p.fillRect(QRectF(bar.x(), bar.y(), 6, bar.height()), YELLOW)
        p.setFont(QFont("Consolas", 12, QFont.Bold))
        typed = query[:int(len(query) * min(1.0, age / 0.5))]
        p.setPen(QColor(255, 255, 255))
        p.drawText(bar.adjusted(18, 0, -110, 0), Qt.AlignLeft | Qt.AlignVCenter,
                   "QUERY > " + typed + ("▌" if int(age * 6) % 2 else ""))
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        p.setPen(YELLOW)
        p.drawText(bar.adjusted(0, 0, -12, 0), Qt.AlignRight | Qt.AlignVCenter, "NET.TRACE")

        # nodes on a ring, cables drawn out from the center
        random.seed(hash(self.addr))
        nodes = []
        for i in range(8):
            a = math.radians(i * 45 + random.uniform(-14, 14))
            rr = R * random.uniform(0.7, 1.0)
            nodes.append((cx + math.cos(a) * rr * 1.5, cy + math.sin(a) * rr, random.uniform(0.25, 1.2),
                          random.choice(("SUBNET", "ARCHIVE", "DATAFORT", "MIRROR", "CACHE", "INDEX")),
                          random.randint(62, 99)))
        random.seed()
        p.setFont(QFont("Consolas", 8, QFont.Bold))
        for i, (nx, ny, t0, name, match) in enumerate(nodes):
            k = ease((age - t0 * 0.5) / 0.35)
            if k <= 0:
                continue
            # cable: right-angled like circuit traces
            mx = cx + (nx - cx) * k
            my = cy + (ny - cy) * k
            p.setPen(QPen(_alpha(col, 120), 1.5))
            p.drawLine(QPointF(cx, cy), QPointF(mx, cy))
            p.drawLine(QPointF(mx, cy), QPointF(mx, my))
            if k < 1:
                continue
            # packets running along the cable, over and over
            for j in range(2):
                f = (age * 1.6 + j * 0.5 + i * 0.13) % 1
                L1, L2 = abs(nx - cx), abs(ny - cy)
                d = f * (L1 + L2)
                px = cx + math.copysign(min(d, L1), nx - cx)
                py = cy + (math.copysign(d - L1, ny - cy) if d > L1 else 0)
                p.fillRect(QRectF(px - 3, py - 3, 6, 6), QColor(255, 255, 255) if j == 0 else YELLOW)
            # node + result tag
            hit = age - (t0 * 0.5 + 0.35)
            p.setPen(QPen(col, 2))
            p.setBrush(QColor(6, 8, 14, 230))
            p.drawRect(QRectF(nx - 9, ny - 9, 18, 18))
            if hit < 0.15:
                p.fillRect(QRectF(nx - 20, ny - 20, 40, 40), _alpha(col, int(160 * (1 - hit / 0.15))))
            p.fillRect(QRectF(nx - 4, ny - 4, 8, 8), YELLOW if match > 90 else col)
            tag = QRectF(nx + (16 if nx >= cx else -166), ny - 24, 150, 20)
            p.fillRect(tag, _alpha(col, 230) if match > 90 else QColor(6, 8, 14, 220))
            p.setPen(QColor(8, 8, 12) if match > 90 else col)
            p.drawText(tag.adjusted(6, 0, -6, 0), Qt.AlignLeft | Qt.AlignVCenter, "%s_%02d" % (name, i))
            p.drawText(tag.adjusted(6, 0, -6, 0), Qt.AlignRight | Qt.AlignVCenter, "%d%%" % match)

        # center: hexagon core with the mark, pulsing as packets leave
        p.save()
        p.translate(cx, cy)
        hexr = 92
        hx = QPainterPath()
        for i in range(6):
            a = math.radians(60 * i + 30)
            pt = QPointF(math.cos(a) * hexr, math.sin(a) * hexr)
            hx.moveTo(pt) if i == 0 else hx.lineTo(pt)
        hx.closeSubpath()
        p.fillPath(hx, QColor(6, 8, 14, 240))
        p.setPen(QPen(col, 2.5))
        p.drawPath(hx)
        p.rotate(age * 50)
        p.setPen(QPen(_alpha(YELLOW, 200), 2))
        p.setBrush(Qt.NoBrush)
        for s in range(3):
            p.drawArc(QRectF(-hexr - 14, -hexr - 14, (hexr + 14) * 2, (hexr + 14) * 2), s * 120 * 16, 50 * 16)
        p.restore()
        s = 120 * (1 + 0.04 * math.sin(age * 12))
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(fade * 0.5)
        p.drawImage(QRectF(cx - s / 2 - 4, cy - s / 2, s, s), self.img_red)
        p.setOpacity(fade)
        p.drawImage(QRectF(cx - s / 2, cy - s / 2, s, s), self.img)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        found = sum(1 for n in nodes if age > n[2] * 0.5 + 0.35)
        p.setFont(QFont("Consolas", 11, QFont.Bold))
        p.setPen(col)
        p.drawText(QRectF(0, cy + R + 30, w, 22), Qt.AlignHCenter | Qt.AlignVCenter,
                   "RASTREANDO A NET  ·  %d/8 NÓS" % found)
        self._paint_quip(p, age - 1.2, cy + R + 58)
        p.end()

    def _paint_download(self, p, age, fade, w, h):
        # download: columns of glyphs rain down into a bucket bar that fills up
        col = self.color
        p.setFont(QFont("Consolas", 12, QFont.Bold))
        random.seed(int(self.start))
        cols = w // 22
        for i in range(cols):
            speed, off, ln = random.uniform(300, 700), random.uniform(0, h), random.randint(5, 14)
            head = (age * speed + off) % (h + ln * 18) - ln * 18
            chars = [random.choice("01アイウエオカキクケコ#$%") for _ in range(ln)]
            for j, ch in enumerate(chars):
                y = head - j * 18
                if 0 < y < h - 70:
                    p.setPen(QColor(255, 255, 255) if j == 0 else _alpha(col, int(200 * (1 - j / ln))))
                    p.drawText(QPointF(i * 22 + 4, y), ch)
        random.seed()
        # bucket bar at the bottom collecting the data
        prog = min(1.0, age / 1.8)
        bar = QRectF(w * 0.15, h - 60, w * 0.7, 16)
        p.fillRect(bar.adjusted(-10, -30, 10, 30), QColor(4, 6, 12, 200))
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar)
        p.fillRect(QRectF(bar.x() + 3, bar.y() + 3, (bar.width() - 6) * prog, 10), col)
        p.setFont(QFont("Consolas", 9, QFont.Bold))
        p.setPen(QColor(255, 255, 255))
        mb = prog * random.uniform(40, 41)
        p.drawText(QRectF(bar.x(), bar.y() - 22, bar.width(), 18), Qt.AlignLeft | Qt.AlignVCenter,
                   "▼ DOWNLOAD CONCLUÍDO" if prog >= 1 else "▼ BAIXANDO  %.1f MB" % mb)
        p.drawText(QRectF(bar.x(), bar.y() - 22, bar.width(), 18), Qt.AlignRight | Qt.AlignVCenter, "%d%%" % (prog * 100))
        self._paint_quip(p, age - 1.0, h / 2)
        p.end()

    def _paint_error(self, p, age, fade, w, h):
        # error: red screen shake, glitch bars and a warning box with the first line of the error
        red = QColor(255, 45, 75)
        shake = random.uniform(-8, 8) * max(0.0, 1 - age / 0.5)
        p.translate(shake, 0)
        if int(age * 10) % 2 and age < 0.8:
            p.fillRect(QRectF(0, 0, w, h), _alpha(red, 30))
        p.setPen(QPen(red, 4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(2, 2, w - 4, h - 4))
        for _ in range(int(8 * max(0.2, 1 - age / 1.2))):  # glitch bars
            y = random.uniform(0, h)
            p.fillRect(QRectF(random.uniform(-50, w * 0.5), y, random.uniform(80, w * 0.6), random.uniform(2, 10)),
                       _alpha(red, random.randint(40, 140)))
        cx, cy = w / 2, h / 2
        bw = min(w - 60, 520)
        box = QRectF(cx - bw / 2, cy - 70, bw, 140)
        p.fillRect(box, QColor(10, 2, 6, 230))
        p.setPen(QPen(red, 2))
        p.drawRect(box)
        p.fillRect(QRectF(box.x(), box.y(), bw, 22), red)
        p.setFont(QFont("Consolas", 9, QFont.Bold))
        p.setPen(QColor(10, 2, 6))
        p.drawText(QRectF(box.x() + 10, box.y(), bw - 20, 22), Qt.AlignLeft | Qt.AlignVCenter, "⚠ SYSTEM FAILURE  //  " + self.addr)
        # triangle with the mark
        tri = QPainterPath()
        tx, ty = box.x() + 52, box.y() + 82
        tri.moveTo(tx, ty - 40); tri.lineTo(tx + 38, ty + 26); tri.lineTo(tx - 38, ty + 26); tri.closeSubpath()
        p.setPen(QPen(red if int(age * 6) % 2 else QColor(255, 255, 255), 2.5))
        p.drawPath(tri)
        p.setFont(QFont("Consolas", 26, QFont.Bold))
        p.drawText(QRectF(tx - 20, ty - 22, 40, 44), Qt.AlignCenter, "!")
        title = "ERRO DE EXECUÇÃO"
        if age < 0.6:
            title = "".join(random.choice(GLYPHS) if random.random() < 0.35 else ch for ch in title)
        p.setFont(QFont("Consolas", 15, QFont.Bold))
        p.setPen(red)
        p.drawText(QRectF(box.x() + 104, box.y() + 32, bw - 116, 26), Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setFont(QFont("Consolas", 9))
        p.setPen(QColor(235, 220, 225))
        detail = self.detail or "falha desconhecida"
        p.drawText(QRectF(box.x() + 104, box.y() + 62, bw - 116, 70), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                   detail[:int(len(detail) * min(1.0, age / 0.6))])
        p.translate(-shake, 0)
        self._paint_quip(p, age - 0.9, box.bottom() + 14)
        p.end()

    def _paint_quip(self, p, t, y):
        if t <= 0:
            return
        text = self.quip[:int(len(self.quip) * min(1.0, t / 0.5))]
        p.setFont(QFont("Consolas", 10))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self.quip) + 24
        box = QRectF(self.width() / 2 - tw / 2, y, tw, 24)
        p.setPen(QPen(_alpha(self.color, 120), 1))
        p.setBrush(QColor(5, 5, 10, 170))
        p.drawRect(box)
        p.setPen(QColor(235, 240, 255))
        p.drawText(box.adjusted(12, 0, -12, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
