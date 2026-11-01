"""Netrunner profile panel (opened from Yui's photo) and the in-app mark creator.

The creator builds an SVG out of a few layers (frame, symbol, rings, circuit traces) so the
result works anywhere a mark SVG does (see assets/sigils).
"""
import math
import os
import random
import re

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QSlider, QVBoxLayout, QWidget)

from assistente import hub, plat, sfx

MONO = "Consolas"
FRAMES = ["Nenhuma", "Círculo", "Hexágono", "Triângulo", "Losango", "Quadrado", "Octógono"]
SYMBOLS = ["Texto", "Olho", "Gatinha", "Raio", "Cruz", "Caveira", "Diamante", "Nenhum"]


def _alpha(c, a):
    c = QColor(c)
    c.setAlpha(a)
    return c


def render_svg(data, size, color):
    """SVG (path or bytes) -> image tinted with color."""
    r = QSvgRenderer(QByteArray(data) if isinstance(data, (bytes, bytearray)) else data)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    if r.isValid():
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, size, size))
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(img.rect(), color)
        p.end()
    return img


def _poly(n, radius, rot=-90):
    pts = [(100 + radius * math.cos(math.radians(rot + i * 360 / n)),
            100 + radius * math.sin(math.radians(rot + i * 360 / n))) for i in range(n)]
    return pts


def _path(pts, closed=True, broken=False):
    if not broken:
        d = "M" + " L".join("%.1f %.1f" % p for p in pts)
        return '<path d="%s%s"/>' % (d, " Z" if closed else "")
    out = []  # a gap in every other edge, like a cracked frame
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        if i % 2:
            mid = (a[0] + (b[0] - a[0]) * 0.35, a[1] + (b[1] - a[1]) * 0.35)
            mid2 = (a[0] + (b[0] - a[0]) * 0.65, a[1] + (b[1] - a[1]) * 0.65)
            out.append("M%.1f %.1f L%.1f %.1f M%.1f %.1f L%.1f %.1f" % (a + mid + mid2 + b))
        else:
            out.append("M%.1f %.1f L%.1f %.1f" % (a + b))
    return '<path d="%s"/>' % " ".join(out)


def build_svg(o):
    sw = o["stroke"]
    parts = []
    frame, broken = o["frame"], o["broken"]
    if frame == "Círculo":
        if broken:
            for k in range(4):
                a0, a1 = math.radians(k * 90 + 10), math.radians(k * 90 + 80)
                parts.append('<path d="M%.1f %.1f A84 84 0 0 1 %.1f %.1f"/>' % (
                    100 + 84 * math.cos(a0), 100 + 84 * math.sin(a0), 100 + 84 * math.cos(a1), 100 + 84 * math.sin(a1)))
        else:
            parts.append('<circle cx="100" cy="100" r="84"/>')
    elif frame != "Nenhuma":
        n, rot = {"Hexágono": (6, -90), "Triângulo": (3, -90), "Losango": (4, -90), "Quadrado": (4, -45),
                  "Octógono": (8, -67.5)}[frame]
        parts.append(_path(_poly(n, 88 if n != 3 else 92, rot), broken=broken))
    if o["double"] and frame != "Nenhuma":
        parts.append('<circle cx="100" cy="100" r="94" stroke-width="%d" stroke-dasharray="4 8"/>' % max(2, sw // 3))

    for i in range(o["rings"]):
        r = 30 + i * 14
        parts.append('<circle cx="100" cy="100" r="%d" stroke-width="%d" stroke-dasharray="%d %d"/>'
                     % (r, max(2, sw // 2), 18 + i * 6, 10 + i * 4))

    rng = random.Random(o["seed"])
    for i in range(o["traces"]):  # circuit traces running out from the middle, each ending in a node
        a = math.radians(i * 360 / max(1, o["traces"]) + rng.uniform(-12, 12))
        r0, r1 = 46, rng.uniform(62, 80)
        x0, y0 = 100 + r0 * math.cos(a), 100 + r0 * math.sin(a)
        x1, y1 = 100 + r1 * math.cos(a), 100 + r1 * math.sin(a)
        bend = math.radians(rng.choice((-35, 35)))
        x2, y2 = x1 + 12 * math.cos(a + bend), y1 + 12 * math.sin(a + bend)
        parts.append('<path d="M%.1f %.1f L%.1f %.1f L%.1f %.1f" stroke-width="%d"/>' % (x0, y0, x1, y1, x2, y2, max(2, sw // 2)))
        parts.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="white" stroke="none"/>' % (x2, y2, max(3, sw // 2 + 1)))

    s, big = o["symbol"], sw * 2
    if s == "Texto":
        text = (o["text"] or "Y")[:3].upper().replace("&", "&amp;").replace("<", "&lt;")
        size = {1: 96, 2: 70, 3: 52}[len(text)]
        parts.append('<text x="100" y="100" text-anchor="middle" dominant-baseline="central" font-family="%s" '
                     'font-weight="bold" font-size="%d" fill="white" stroke="none">%s</text>' % (o["font"], size, text))
    elif s == "Olho":
        parts.append('<path d="M40 100 Q100 50 160 100 Q100 150 40 100 Z" stroke-width="%d"/>' % big)
        parts.append('<circle cx="100" cy="100" r="16" fill="white" stroke="none"/>')
    elif s == "Gatinha":
        parts.append('<path d="M58 140 L58 72 L78 92 L122 92 L142 72 L142 140 Z" stroke-width="%d"/>' % big)
        parts.append('<path d="M82 116 L92 112 M118 116 L108 112" stroke-width="%d"/>' % big)
    elif s == "Raio":
        parts.append('<path d="M114 40 L74 108 L104 108 L86 162 L130 88 L100 88 Z" fill="white" stroke="none"/>')
    elif s == "Cruz":
        parts.append('<path d="M100 48 L100 152 M48 100 L152 100" stroke-width="%d"/>' % big)
        parts.append('<rect x="88" y="88" width="24" height="24" fill="white" stroke="none" transform="rotate(45 100 100)"/>')
    elif s == "Caveira":
        parts.append('<path d="M62 100 Q62 52 100 52 Q138 52 138 100 L138 118 L122 126 L122 146 L78 146 L78 126 L62 118 Z" stroke-width="%d"/>' % big)
        parts.append('<circle cx="84" cy="102" r="10" fill="white" stroke="none"/><circle cx="116" cy="102" r="10" fill="white" stroke="none"/>')
        parts.append('<path d="M92 146 L92 132 M108 146 L108 132" stroke-width="%d"/>' % sw)
    elif s == "Diamante":
        parts.append('<path d="M100 46 L146 100 L100 154 L54 100 Z" stroke-width="%d"/>' % big)
        parts.append('<path d="M100 72 L122 100 L100 128 L78 100 Z" fill="white" stroke="none"/>')

    body = "\n  ".join(parts)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" fill="none" stroke="white" '
            'stroke-width="%d" stroke-linejoin="miter" stroke-linecap="square">\n'
            '  <g transform="rotate(%d 100 100)">\n  %s\n  </g>\n</svg>\n' % (sw, o["rotation"], body)).encode("utf-8")


def _app():
    from assistente import app  # imported late: app.py opens this module, not the other way round
    return app


def style(accent):
    a, f = accent.name(), _app().FONT_FAMILY
    return f"""
        QWidget {{ color: rgb(255,250,253); font-family: "{f}"; font-size: 12px; background: transparent; }}
        QLabel#head {{ color: rgb(255,250,253); font-size: 17px; font-weight: 600; }}
        QLabel#section {{ color: rgba(240,228,250,165); font-size: 11px; font-weight: 600; }}
        QLabel#sub {{ color: rgba(240,228,250,165); font-size: 11px; }}
        QComboBox, QLineEdit {{ background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,40);
                               border-radius: 10px; padding: 5px 10px; }}
        QComboBox:focus, QLineEdit:focus {{ border: 1px solid {a}; }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{ background: rgba(40,26,58,250); border: 1px solid rgba(255,210,240,80);
                                      border-radius: 8px; selection-background-color: {a}; outline: none; }}
        QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: rgba(255,255,255,30); }}
        QSlider::sub-page:horizontal {{ border-radius: 3px; background: {a}; }}
        QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -5px 0; border-radius: 8px;
                                     background: white; }}
        QCheckBox {{ spacing: 7px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,90); }}
        QCheckBox::indicator:checked {{ background: {a}; border: 1px solid {a}; }}
    """


class CyberFrame(QWidget):
    """Panel body in the app's look: smoky Liquid Glass with a soft rim and a title on top."""

    def __init__(self, accent, title, parent=None):
        super().__init__(parent)
        self.accent, self.title = accent, title

    def paintEvent(self, _):
        a = _app()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.fillPath(path, QColor(26, 16, 40, 246))
        glow = QRadialGradient(QPointF(r.width() * 0.15, 0), r.width() * 0.9)
        glow.setColorAt(0, _alpha(a.ACCENT, 60))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillPath(path, glow)
        a.paint_glass(p, r, 20, a.rgba(255, 255, 255, 20), a.rgba(255, 255, 255, 6), a.rgba(255, 255, 255, 80),
                      a.rgba(255, 255, 255, 14))
        p.setPen(a.TEXT)
        p.setFont(a.font(13, QFont.DemiBold))
        p.drawText(QRectF(20, 10, r.width() - 40, 22), Qt.AlignVCenter, self.title)
        p.end()


class NeonButton(QAbstractButton):
    """Glass pill button, same as the app's quick replies."""

    def __init__(self, text, accent, danger=False):
        super().__init__()
        self.setText(text)
        self.accent = QColor(220, 70, 100) if danger else accent
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.hover = False
        self.clicked.connect(lambda: sfx.play("click"))

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 30, 30)

    def enterEvent(self, e):
        self.hover = True; self.update()

    def leaveEvent(self, e):
        self.hover = False; self.update()

    def paintEvent(self, _):
        a = _app()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c, enabled = self.accent, self.isEnabled()
        boost = 30 if self.hover and enabled else 0
        a.paint_glass(p, r, r.height() / 2, a.tint(c, (50 if enabled else 15) + boost, 130),
                      a.tint(c, (20 if enabled else 6) + boost, 130), a.tint(c, 140 if enabled else 50, 160),
                      a.tint(c, 30, 140), backing=a.SMOKE)
        p.setPen(_alpha(a.TEXT, 255 if enabled else 90))
        p.setFont(a.font(12, QFont.DemiBold))
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class MarkTile(QAbstractButton):
    def __init__(self, path, accent, selected):
        super().__init__()
        self.path, self.accent, self.selected = path, accent, selected
        self.setFixedSize(64, 78)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(os.path.basename(path))
        self.img = render_svg(path, 40, QColor(255, 250, 253) if not selected else accent)
        self.hover = False

    def enterEvent(self, e):
        self.hover = True; self.update(); sfx.play("click")

    def leaveEvent(self, e):
        self.hover = False; self.update()

    def paintEvent(self, _):
        a = _app()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.accent
        box = QRectF(2, 2, 60, 58)
        if self.selected:
            a.paint_glass(p, box, 16, a.tint(c, 70, 130), a.tint(c, 30, 130), c, a.tint(c, 60, 140), backing=a.SMOKE)
        else:
            boost = 16 if self.hover else 0
            a.paint_glass(p, box, 16, a.rgba(255, 255, 255, 20 + boost), a.rgba(255, 255, 255, 6 + boost),
                          a.rgba(255, 255, 255, 60), a.rgba(255, 255, 255, 10), backing=a.SMOKE)
        p.drawImage(QPointF(12, 11), self.img)
        p.setFont(a.font(10, QFont.DemiBold if self.selected else QFont.Normal))
        p.setPen(a.TEXT if self.selected else a.SUBTEXT)
        p.drawText(QRectF(0, 62, 64, 16), Qt.AlignCenter, os.path.basename(self.path)[:-4][:10].capitalize())
        p.end()


def _round_photo(path, size, ring):
    src = QPixmap(path).scaled(size - 6, size - 6, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(ring, 2.5))
    p.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    clip = QPainterPath()
    clip.addEllipse(QRectF(3, 3, size - 6, size - 6))
    p.setClipPath(clip)
    p.drawPixmap(3 + (size - 6 - src.width()) // 2, 3 + (size - 6 - src.height()) // 2, src)
    p.end()
    return out


class ProfilePanel(QWidget):
    """Popup opened from Yui's photo: photo, mark picker, mark creator, sound settings."""

    def __init__(self, win, sigils_dir, accent):
        super().__init__(win, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.win, self.dir, self.accent = win, sigils_dir, accent
        self.setStyleSheet(style(accent))
        self.frame = CyberFrame(accent, "Perfil", self)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(18, 42, 18, 18)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(14)
        self.photo = QLabel()
        self.photo.setFixedSize(84, 84)
        self.photo.setPixmap(_round_photo(win.photo_path(), 84, accent))
        top.addWidget(self.photo)
        info = QVBoxLayout()
        info.setSpacing(2)
        head = QLabel(hub.NAME); head.setObjectName("head")
        sub = QLabel("assistente pessoal · online"); sub.setObjectName("sub")
        info.addWidget(head); info.addWidget(sub)
        row = QHBoxLayout()
        b = NeonButton("Trocar foto", accent); b.clicked.connect(lambda: self._run(win.choose_photo)); row.addWidget(b)
        b = NeonButton("Padrão", accent); b.setEnabled(bool(win.settings.get("photo")))
        b.clicked.connect(lambda: self._run(lambda: win.set_photo(""))); row.addWidget(b)
        info.addLayout(row)
        b = NeonButton("Configurar assistente", accent); b.clicked.connect(lambda: self._run(win.open_settings))
        info.addWidget(b)
        top.addLayout(info, 1)
        lay.addLayout(top)

        lab = QLabel("MARCA"); lab.setObjectName("section")
        lay.addWidget(lab)
        grid = QGridLayout(); grid.setSpacing(6)
        current = os.path.normcase(win.sigil_path())
        names = sorted(n for n in os.listdir(sigils_dir) if n.lower().endswith(".svg"))
        for i, name in enumerate(names):
            path = os.path.join(sigils_dir, name)
            tile = MarkTile(path, accent, os.path.normcase(path) == current)
            tile.clicked.connect(lambda _=False, p=path: self._pick(p))
            grid.addWidget(tile, i // 5, i % 5)
        lay.addLayout(grid)
        row = QHBoxLayout()
        for text, fn in (("Criar marca", self._create), ("Importar SVG", lambda: self._run(win.choose_sigil)),
                         ("Pasta", lambda: plat.open_path(sigils_dir)), ("Testar", self._test)):
            b = NeonButton(text, accent); b.clicked.connect(fn); row.addWidget(b)
        lay.addLayout(row)

        lab = QLabel("EFEITOS SONOROS"); lab.setObjectName("section")
        lay.addWidget(lab)
        row = QHBoxLayout()
        self.on = QCheckBox("efeitos sonoros")
        self.on.setChecked(win.settings.get("sfx_on", True))
        self.on.toggled.connect(self._sound)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(win.settings.get("sfx_volume", 25)))
        self.vol.sliderReleased.connect(self._sound)
        row.addWidget(self.on); row.addWidget(self.vol, 1)
        lay.addLayout(row)

        lab = QLabel("VISÃO"); lab.setObjectName("section")
        lay.addWidget(lab)
        self.eyes = QCheckBox("visão local (uns 4 GB da placa de vídeo)")
        self.eyes.setChecked(win.settings.get("eyes_on", True))
        self.eyes.toggled.connect(self._eyes)
        lay.addWidget(self.eyes)
        hint = QLabel("Desligada, ela comenta a tela com frases prontas."); hint.setObjectName("sub")
        lay.addWidget(hint)
        self.frame.setFixedWidth(376)
        self.frame.adjustSize()
        self.resize(self.frame.size())
        sfx.play("open")

    def _run(self, fn):
        self.close()
        fn()

    def _pick(self, path):
        self.close()
        self.win.set_sigil(path)

    def _test(self):
        self.close()
        if getattr(self.win, "netrun", None):
            self.win.netrun.preview_sigil()

    def _create(self):
        self.close()
        dlg = SigilStudio(self.win, self.dir, self.accent)
        if dlg.exec() and dlg.saved:
            self.win.set_sigil(dlg.saved)

    def _eyes(self, on):
        from assistente import eyes
        self.win.settings["eyes_on"] = on
        self.win.save_settings()
        eyes.set_enabled(on)
        sfx.play("click")

    def _sound(self, *_):
        self.win.settings["sfx_on"] = self.on.isChecked()
        self.win.settings["sfx_volume"] = self.vol.value()
        self.win.save_settings()
        sfx.enabled = self.on.isChecked()
        sfx.set_volume(self.vol.value() / 100)
        sfx.play("click")


class Preview(QWidget):
    def __init__(self, accent):
        super().__init__()
        self.accent, self.img, self.t = accent, None, 0
        self.setFixedSize(240, 240)
        QTimer(self, interval=40, timeout=self._tick).start()

    def set_svg(self, data):
        self.img = render_svg(data, 180, self.accent)
        self.red = render_svg(data, 180, QColor(255, 40, 90))
        self.update()

    def _tick(self):
        self.t += 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.accent
        p.fillRect(self.rect(), QColor(255, 255, 255, 6))
        p.setPen(QPen(_alpha(c, 25), 1))
        for i in range(0, 240, 20):  # grid like a design table
            p.drawLine(i, 0, i, 240); p.drawLine(0, i, 240, i)
        p.save()
        p.translate(120, 120)
        p.rotate(self.t * 2)
        p.setPen(QPen(_alpha(c, 90), 1.5))
        for k in range(3):
            p.drawArc(QRectF(-112, -112, 224, 224), k * 120 * 16, 70 * 16)
        p.restore()
        if self.img:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.setOpacity(0.4)
            p.drawImage(QPointF(27, 30), self.red)
            p.setOpacity(0.9 + 0.1 * math.sin(self.t * 0.6))
            p.drawImage(QPointF(30, 30), self.img)
        p.end()


class SigilStudio(QDialog):
    def __init__(self, win, sigils_dir, accent):
        super().__init__(win, Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.dir, self.accent, self.saved = sigils_dir, accent, None
        self.setStyleSheet(style(accent))
        self.frame = CyberFrame(accent, "Criar marca", self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(self.frame)
        lay = QHBoxLayout(self.frame)
        lay.setContentsMargins(16, 36, 16, 16)
        lay.setSpacing(16)

        left = QVBoxLayout()
        self.preview = Preview(accent)
        left.addWidget(self.preview)
        self.name = QLineEdit(); self.name.setPlaceholderText("nome da marca")
        left.addWidget(self.name)
        left.addStretch(1)
        lay.addLayout(left)

        form = QGridLayout(); form.setVerticalSpacing(8); form.setHorizontalSpacing(10)
        self.frame_box = QComboBox(); self.frame_box.addItems(FRAMES)
        self.symbol = QComboBox(); self.symbol.addItems(SYMBOLS)
        self.text = QLineEdit("Y"); self.text.setMaxLength(3)
        self.font_box = QComboBox(); self.font_box.addItems(["Consolas", "Impact", "Segoe UI Black", "Courier New", "Arial Black"])
        self.broken = QCheckBox("moldura rachada")
        self.double = QCheckBox("contorno pontilhado")
        self.rings = self._slider(0, 3, 1)
        self.traces = self._slider(0, 10, 4)
        self.stroke = self._slider(3, 12, 6)
        self.rotation = self._slider(0, 359, 0)
        rows = (("MOLDURA", self.frame_box), ("", self.broken), ("", self.double), ("SÍMBOLO", self.symbol),
                ("TEXTO", self.text), ("FONTE", self.font_box), ("ANÉIS", self.rings), ("CIRCUITOS", self.traces),
                ("TRAÇO", self.stroke), ("GIRO", self.rotation))
        for i, (label, w) in enumerate(rows):
            l = QLabel(label); l.setObjectName("sub")
            form.addWidget(l, i, 0); form.addWidget(w, i, 1)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addStretch(1)
        btns = QHBoxLayout()
        b = NeonButton("Aleatória", accent); b.clicked.connect(self.randomize); btns.addWidget(b)
        b = NeonButton("Cancelar", accent, danger=True); b.clicked.connect(self.reject); btns.addWidget(b)
        b = NeonButton("Salvar", accent); b.clicked.connect(self.save); btns.addWidget(b)
        right.addLayout(btns)
        lay.addLayout(right, 1)

        self.seed = random.randint(0, 9999)
        self.frame_box.setCurrentText("Hexágono"); self.broken.setChecked(True)
        for w in (self.frame_box, self.symbol, self.font_box):
            w.currentIndexChanged.connect(self.refresh)
        for w in (self.broken, self.double):
            w.toggled.connect(self.refresh)
        self.text.textChanged.connect(self.refresh)
        self.resize(640, 420)
        self.refresh()
        sfx.play("open")

    def _slider(self, lo, hi, val):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi); s.setValue(val)
        s.valueChanged.connect(self.refresh)
        return s

    def options(self):
        return {"frame": self.frame_box.currentText(), "broken": self.broken.isChecked(), "double": self.double.isChecked(),
                "symbol": self.symbol.currentText(), "text": self.text.text().strip(), "font": self.font_box.currentText(),
                "rings": self.rings.value(), "traces": self.traces.value(), "stroke": self.stroke.value(),
                "rotation": self.rotation.value(), "seed": self.seed}

    def refresh(self, *_):
        if not hasattr(self, "seed"):
            return
        self.text.setEnabled(self.symbol.currentText() == "Texto")
        self.font_box.setEnabled(self.symbol.currentText() == "Texto")
        self.preview.set_svg(build_svg(self.options()))

    def randomize(self):
        self.seed = random.randint(0, 9999)
        widgets = (self.frame_box, self.symbol, self.broken, self.double, self.rings, self.traces, self.stroke)
        for w in widgets:
            w.blockSignals(True)
        self.frame_box.setCurrentIndex(random.randint(1, len(FRAMES) - 1))
        self.symbol.setCurrentIndex(random.randint(0, len(SYMBOLS) - 2))
        self.broken.setChecked(random.random() < 0.6)
        self.double.setChecked(random.random() < 0.3)
        self.rings.setValue(random.randint(0, 2))
        self.traces.setValue(random.choice((0, 3, 4, 6, 8)))
        self.stroke.setValue(random.randint(4, 9))
        for w in widgets:
            w.blockSignals(False)
        sfx.play("glitch")
        self.refresh()

    def save(self):
        name = re.sub(r"[^\w\- ]", "", self.name.text().strip()).strip() or "marca-%d" % self.seed
        path = os.path.join(self.dir, name + ".svg")
        with open(path, "wb") as f:
            f.write(build_svg(self.options()))
        self.saved = path
        self.accept()

    def mousePressEvent(self, e):  # drag the frameless dialog around
        self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
