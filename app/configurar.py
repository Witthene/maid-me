"""Maid Me configurator: a window to set up the assistant (name, personality, brain, voice...).

Saves everything to .env and builds persona.md; personalidade.txt keeps the free-text personality.
Icons are glyphs from Windows' own vector icon font (Segoe Fluent Icons, or MDL2 on Windows 10).
"""
import os
import random
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from functools import lru_cache
from tkinter import messagebox

import customtkinter as ctk
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from assistente import __version__, persona  # noqa: E402
from assistente.brain import PROVIDERS, list_models, quick_test  # noqa: E402

ENV = os.path.join(ROOT, ".env")
GUIDE = os.path.join(os.path.dirname(ROOT), "GUIA.html")
CUSTOM = "Personalizada"
WHISPER = ["large-v3-turbo", "medium", "small", "base"]
EFFORTS = ["low", "medium", "high"]
DEFAULTS = {"ASSISTANT_NAME": "", "WAKE_ALIASES": "", "USER_NAME": "", "USER_PRONOUN": "neutro",
            "PERSONALITY": "Sarcástica e engraçada", "BRAIN": "gemini", "BRAIN_API_KEY": "", "BRAIN_MODEL": "",
            "BRAIN_BASE_URL": "", "CLAUDE_EFFORT": "low", "VOICE": "edge", "VOICE_API_KEY": "",
            "VOICE_ID": "pt-BR-FranciscaNeural", "VOICE_MODEL": "", "MIC_DEVICE": "",
            "WHISPER_MODEL": "large-v3-turbo", "VTUBE_STUDIO": "0", "OVERLAY": "0", "OVERLAY_SCALE": "0.5",
            "WATCH": "0", "FOLLOWUP_SECONDS": "20", "IDLE_RESET_MINUTES": "10"}

# --- look -------------------------------------------------------------------------
BG, SIDEBAR, CARD, FIELD = "#17131f", "#211a2e", "#2a2238", "#352b47"
PINK, PINK_HOVER, PURPLE, PURPLE_HOVER = "#ff5fa8", "#e04790", "#8f6bff", "#7655e6"
TEXT, MUTED, OK, ERROR = "#f4eefc", "#a99cc0", "#7ee0a6", "#ff7a8a"
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")


def load_fonts():
    """Poppins, loaded only for this process (nothing gets installed in Windows)."""
    import ctypes
    files = [os.path.join(FONTS_DIR, f) for f in os.listdir(FONTS_DIR) if f.endswith(".ttf")] \
        if os.path.isdir(FONTS_DIR) else []
    loaded = sum(ctypes.windll.gdi32.AddFontResourceExW(f, 0x10, 0) for f in files)  # FR_PRIVATE
    return loaded > 0


HAS_POPPINS = load_fonts()
FONT = "Poppins" if HAS_POPPINS else "Segoe UI"
FONT_MEDIUM = "Poppins Medium" if HAS_POPPINS else "Segoe UI"
FONT_SEMI = "Poppins SemiBold" if HAS_POPPINS else "Segoe UI Semibold"
TAGS = {"Grátis": ("#1f4535", OK), "Pago": ("#3d2f55", "#c9b6ff"), "Assinatura": ("#4a2440", "#ffb3d6"),
        "Local": ("#24384f", "#9ccbff"), "Avançado": ("#3a3346", MUTED)}

# Segoe Fluent Icons code points
I_PERSON, I_BULB, I_SPEAKER, I_MIC, I_SPARKLE = "\uE77B", "\uEA80", "\uE767", "\uE720", "\uF4A5"
I_CHECK, I_PLAY, I_SAVE, I_HELP, I_LOCK, I_INFO = "\uE73E", "\uE768", "\uE74E", "\uE897", "\uE72E", "\uE946"
I_CARD, I_SMILE, I_ROBOT, I_GEAR, I_LIST, I_BOLT = "\uE8D4", "\uED54", "\uE99A", "\uE713", "\uE71D", "\uE945"
I_WAVE, I_CHAT, I_GLOBE, I_SEARCH, I_LAPTOP, I_GAME = "\uF61F", "\uE8BD", "\uE774", "\uE721", "\uE7F8", "\uE7FC"
I_VIDEO, I_STAR, I_ALERT, I_AVATAR, I_EDIT, I_PUZZLE = "\uE714", "\uE734", "\uE8C9", "\uEA8C", "\uE70F", "\uEA86"

PAGES = [
    ("identity", I_PERSON, "Identidade", "Dá um nome, um jeitinho e conta um pouco de você pra ela."),
    ("brain", I_BULB, "Inteligência", "Quem vai pensar por ela. Cada um usa a própria chave, nada de filar a dos outros."),
    ("voice", I_SPEAKER, "Voz", "Como ela vai falar. Tem voz grátis e tem voz chique paga."),
    ("mic", I_MIC, "Microfone", "Pra ela te ouvir direitinho. Ou pelo menos fingir que ouviu."),
    ("extras", I_SPARKLE, "Avatar e extras", "Corpinho, manias e outras gracinhas. Tudo opcional, prometo."),
]
TIPS = {  # a random one shows up each time you open the page
    "identity": [
        "Escolhe um nome curto. Ninguém quer gritar \"Ei, Maria Eduarda Fernanda\" pro computador.",
        "Nome parecido com palavra comum faz ela acordar sozinha no meio da sua call. Fica a dica.",
        "A personalidade pronta é só o começo. Edita a descrição e cria a sua criatura.",
        "\"Coisas sobre você\" é onde você conta que é péssimo em jogo de tiro. Ela vai lembrar. Pra sempre.",
    ],
    "brain": [
        "Sem grana? O Gemini tem uso grátis. De nada.",
        "Aperta \"Testar conexão\" antes de sair falando com ela. Confia em mim.",
        "Modelo pequeno é rápido, mas às vezes faz besteira no seu PC. Escolhe com carinho.",
        "Sua chave de API é tipo a senha do Wi-Fi: não sai distribuindo por aí.",
    ],
    "voice": [
        "A voz do Edge é grátis e quebra um galho enorme. Rica você fica depois.",
        "Quer voz de personagem de anime? A Fish Audio tem um monte.",
        "Clica em \"Ouvir a voz\" e vê se combina com a personalidade. Tsundere com voz de GPS não dá.",
    ],
    "mic": [
        "Se ela não te ouve, o culpado quase sempre é o microfone errado aqui. Não é birra, juro.",
        "Sem placa NVIDIA ela entende um pouquinho mais devagar. Paciência é uma virtude.",
        "Fone com microfone ajuda ela a não confundir a própria voz com a sua.",
    ],
    "extras": [
        "Com o VTube Studio ela ganha corpinho, mexe a boca e faz careta pra você.",
        "O avatar flutuante fica por cima de tudo. Inclusive do seu jogo. Não diga que eu não avisei.",
        "Ligou o comentário de vídeos? Prepare-se pra opiniões que você não pediu.",
    ],
}
PRIVACY = ("Suas chaves e configurações ficam salvas apenas neste computador, no arquivo .env da pasta do programa. "
           "Nada é enviado a terceiros além do serviço de IA e de voz que você escolher.")

PERSONALITY_TILES = {"Sarcástica e engraçada": I_SMILE, "Fofa e animada": I_STAR, "Calma e profissional": I_CARD,
                     "Tsundere": I_ALERT, "Mordomo chique": I_AVATAR, "Parceiro gamer": I_GAME, CUSTOM: I_EDIT}
BRAIN_TILES = {  # provider: icon, short name, tag
    "gemini": (I_SPARKLE, "Google Gemini", "Grátis"),
    "claude-code": (I_ROBOT, "Claude", "Assinatura"),
    "anthropic": (I_ROBOT, "Claude API", "Pago"),
    "openai": (I_CHAT, "ChatGPT", "Pago"),
    "openrouter": (I_GLOBE, "OpenRouter", "Grátis"),
    "groq": (I_BOLT, "Groq", "Grátis"),
    "deepseek": (I_SEARCH, "DeepSeek", "Pago"),
    "ollama": (I_LAPTOP, "Ollama", "Local"),
    "custom": (I_GEAR, "Outro", "Avançado"),
}
BRAIN_HINTS = {
    "claude-code": "Usa a sua assinatura do Claude (Pro/Max), sem chave. Precisa do Claude Code instalado e logado: veja o guia.",
    "ollama": "Roda no seu PC, sem chave. Instale o Ollama e baixe um modelo que use ferramentas (ex.: qwen3).",
    "gemini": "Tem uso gratuito: pegue a chave no Google AI Studio (aistudio.google.com).",
    "openrouter": "Uma chave só para vários modelos, alguns gratuitos.",
    "groq": "Muito rápido e com uso gratuito.",
    "custom": "Qualquer serviço compatível com a API da OpenAI: preencha o endereço em Avançado.",
}
VOICE_TILES = {
    "edge": (I_SPEAKER, "Microsoft Edge", "Grátis"),
    "fish": (I_WAVE, "Fish Audio", "Pago"),
    "elevenlabs": (I_WAVE, "ElevenLabs", "Pago"),
    "none": (I_CHAT, "Só texto", "Grátis"),
}

ctk.set_appearance_mode("dark")


@lru_cache(maxsize=None)
def _icon_font(px):
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    name = "SegoeIcons.ttf" if os.path.exists(os.path.join(fonts, "SegoeIcons.ttf")) else "segmdl2.ttf"
    return ImageFont.truetype(os.path.join(fonts, name), px)


@lru_cache(maxsize=None)
def icon(glyph, size=18, color=TEXT):
    """A vector glyph rendered at 3x and shown as a crisp CTkImage."""
    px = size * 3
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _icon_font(px)
    box = draw.textbbox((0, 0), glyph, font=font)
    draw.text(((px - box[2] - box[0]) / 2, (px - box[3] - box[1]) / 2), glyph, font=font, fill=color)
    return ctk.CTkImage(img, size=(size, size))


def read_file(name, default=""):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def mic_names():
    try:
        import sounddevice as sd
        apis = sd.query_hostapis()
        return [d["name"] for d in sd.query_devices()
                if d["max_input_channels"] > 0 and "WASAPI" in apis[d["hostapi"]]["name"]]
    except Exception:
        return []


def bind_click(widget, command):
    widget.bind("<Button-1>", lambda _: command())
    try:
        widget.configure(cursor="hand2")
    except (ValueError, tk.TclError):
        pass
    for child in widget.winfo_children():
        bind_click(child, command)


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title(f"Maid Me {__version__} - Configuração")
        self.geometry("1040x720")
        self.minsize(940, 640)
        # customtkinter sets its own icon right after start, so ours goes in a moment later
        self.after(250, lambda: self.iconbitmap(os.path.join(ROOT, "assets", "maidme.ico")))
        cfg = {**DEFAULTS, **{k: v for k, v in dotenv_values(ENV).items() if v is not None}}
        self.v = {k: tk.StringVar(value=v) for k, v in cfg.items()}
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.build_sidebar()
        self.pages, self.tips = {}, {}
        for i, (key, glyph, title, subtitle) in enumerate(PAGES):
            page = ctk.CTkScrollableFrame(self, fg_color=BG, scrollbar_button_color=CARD)
            page.grid_columnconfigure(0, weight=1)
            self.page_header(page, i, glyph, title, subtitle)
            getattr(self, "page_" + key)(page)
            self.tips[key] = self.tip_bubble(page)
            self.pages[key] = page
        for var in (self.v["ASSISTANT_NAME"], self.v["BRAIN_API_KEY"], self.v["BRAIN"]):
            var.trace_add("write", lambda *_: self.refresh_nav())
        self.current = None
        self.show("identity")

    # --- frame -----------------------------------------------------------------
    def build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=240)
        bar.grid(row=0, column=0, sticky="ns")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=20, pady=(26, 0), sticky="w")
        logo = Image.open(os.path.join(ROOT, "assets", "logo.png"))
        ctk.CTkLabel(brand, text="", image=ctk.CTkImage(logo, size=(44, 44))).pack(side="left")
        ctk.CTkLabel(brand, text="Maid Me", font=(FONT, 22, "bold"), text_color=TEXT).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(bar, text="sua assistente, do seu jeitinho", font=(FONT, 11), text_color=MUTED).grid(
            row=1, column=0, padx=22, pady=(6, 18), sticky="w")
        ctk.CTkLabel(bar, text="CONFIGURAÇÃO", font=(FONT_SEMI, 9), text_color=MUTED).grid(
            row=2, column=0, padx=24, pady=(0, 4), sticky="w")
        self.nav = {}
        for i, (key, glyph, label, _) in enumerate(PAGES):
            b = ctk.CTkButton(bar, text=f"  {label}", image=icon(glyph, 18, MUTED), compound="left", anchor="w",
                              height=42, corner_radius=10, font=(FONT_MEDIUM, 13), fg_color="transparent",
                              hover_color=CARD, text_color=MUTED, command=lambda k=key: self.show(k))
            b.grid(row=3 + i, column=0, padx=14, pady=2, sticky="ew")
            self.nav[key] = b
        bar.grid_rowconfigure(10, weight=1)
        ctk.CTkButton(bar, text="  Guia de instalação", image=icon(I_HELP, 16), compound="left", height=36, font=(FONT_MEDIUM, 12),
                      corner_radius=10, fg_color="transparent", border_width=1, border_color=FIELD, hover_color=CARD,
                      text_color=TEXT, command=lambda: webbrowser.open(GUIDE)).grid(
            row=11, column=0, padx=18, pady=4, sticky="ew")
        ctk.CTkButton(bar, text="  Salvar", image=icon(I_SAVE, 16, "white"), compound="left", height=36, font=(FONT_MEDIUM, 12),
                      corner_radius=10, fg_color=PURPLE, hover_color=PURPLE_HOVER, command=self.save).grid(
            row=12, column=0, padx=18, pady=4, sticky="ew")
        ctk.CTkButton(bar, text="  Salvar e iniciar", image=icon(I_PLAY, 16, "white"), compound="left", height=44,
                      corner_radius=10, fg_color=PINK, hover_color=PINK_HOVER, font=(FONT_SEMI, 13),
                      command=self.save_and_start).grid(row=13, column=0, padx=18, pady=4, sticky="ew")
        foot = ctk.CTkFrame(bar, fg_color="transparent")
        foot.grid(row=14, column=0, pady=(8, 18))
        ctk.CTkLabel(foot, text="", image=icon(I_LOCK, 12, MUTED)).pack(side="left")
        ctk.CTkLabel(foot, text=f" Versão {__version__}  ·  Código aberto (MIT)", font=(FONT, 10),
                     text_color=MUTED).pack(side="left")

    def step_done(self, key):
        if key == "identity":
            return bool(self.v["ASSISTANT_NAME"].get().strip())
        if key == "brain":
            return self.v["BRAIN"].get() in ("claude-code", "ollama") or bool(self.v["BRAIN_API_KEY"].get().strip())
        return key in ("voice", "mic", "extras")

    def refresh_nav(self):
        for key, glyph, _, _ in PAGES:
            active = key == self.current
            done = self.step_done(key) and key in ("identity", "brain")
            color = TEXT if active else MUTED
            self.nav[key].configure(fg_color=CARD if active else "transparent", text_color=color,
                                    image=icon(I_CHECK, 18, OK) if done else icon(glyph, 18, PINK if active else MUTED))

    def show(self, key):
        self.current = key
        for page in self.pages.values():
            page.grid_forget()
        self.pages[key].grid(row=0, column=1, sticky="nsew", padx=(24, 24), pady=(0, 16))
        self.tips[key].configure(text=random.choice(TIPS[key]))
        self.refresh_nav()

    def page_header(self, page, i, glyph, title, subtitle):
        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w", padx=8, pady=(22, 16))
        badge = ctk.CTkFrame(header, width=56, height=56, corner_radius=16, fg_color=PURPLE)
        badge.pack(side="left")
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="", image=icon(glyph, 28, "white")).pack(expand=True)
        text = ctk.CTkFrame(header, fg_color="transparent")
        text.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(text, text=f"PASSO {i + 1} DE {len(PAGES)}", font=(FONT_SEMI, 10), text_color=PINK,
                     height=16).pack(anchor="w")
        ctk.CTkLabel(text, text=title, font=(FONT_SEMI, 22), text_color=TEXT, height=34).pack(anchor="w")
        ctk.CTkLabel(text, text=subtitle, font=(FONT, 12), text_color=MUTED, height=20).pack(anchor="w")

    def tip_bubble(self, page):
        bubble = ctk.CTkFrame(page, fg_color=SIDEBAR, corner_radius=12, border_width=1, border_color=FIELD)
        bubble.grid(row=98, column=0, sticky="ew", padx=8, pady=(10, 4))
        bubble.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bubble, text="", image=icon(I_SPARKLE, 22, PINK)).grid(row=0, column=0, padx=(16, 10), pady=12)
        tip = ctk.CTkLabel(bubble, text="", font=(FONT, 12), text_color=TEXT, justify="left", wraplength=600)
        tip.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=12)
        privacy = ctk.CTkFrame(page, fg_color="transparent")
        privacy.grid(row=99, column=0, sticky="w", padx=10, pady=(6, 20))
        ctk.CTkLabel(privacy, text="", image=icon(I_LOCK, 13, MUTED)).pack(side="left", anchor="n", padx=(0, 6))
        ctk.CTkLabel(privacy, text=PRIVACY, font=(FONT, 10), text_color=MUTED, justify="left",
                     wraplength=640).pack(side="left")
        return tip

    def card(self, page, row, glyph, title):
        card = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14)
        card.grid(row=row, column=0, sticky="ew", padx=8, pady=8)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(head, text="", image=icon(glyph, 18, PINK)).pack(side="left")
        ctk.CTkLabel(head, text=title, font=(FONT_SEMI, 14), text_color=TEXT).pack(side="left", padx=(8, 0))
        card.next_row = 1
        return card

    def label(self, card, text):
        ctk.CTkLabel(card, text=text, font=(FONT_MEDIUM, 12), text_color=TEXT).grid(
            row=card.next_row, column=0, sticky="w", padx=18, pady=(10, 4))
        card.next_row += 1

    def hint(self, card, text, color=MUTED):
        label = ctk.CTkLabel(card, text=text, font=(FONT, 11), text_color=color, justify="left", wraplength=640)
        label.grid(row=card.next_row, column=0, sticky="w", padx=18, pady=(2, 0))
        card.next_row += 1
        return label

    def field(self, card, label, widget_factory, hint=None):
        self.label(card, label)
        widget = widget_factory(card)
        widget.grid(row=card.next_row, column=0, sticky="ew", padx=18)
        card.next_row += 1
        if hint:
            self.hint(card, hint)
        return widget

    def end(self, card):
        ctk.CTkFrame(card, height=14, fg_color="transparent").grid(row=card.next_row, column=0)
        card.next_row += 1

    # --- widgets -----------------------------------------------------------------
    def entry(self, key, secret=False):
        return lambda parent: ctk.CTkEntry(parent, textvariable=self.v[key], show="•" if secret else "", height=38,
                                           corner_radius=10, fg_color=FIELD, border_color=FIELD, text_color=TEXT,
                                           font=(FONT, 12))

    def choice(self, var, values, command=None):
        return lambda parent: ctk.CTkOptionMenu(
            parent, variable=var, values=values or [""], height=38, corner_radius=10, fg_color=FIELD,
            button_color=PURPLE, button_hover_color=PURPLE_HOVER, dropdown_fg_color=CARD,
            dropdown_hover_color=FIELD, text_color=TEXT, command=command, dynamic_resizing=False,
            font=(FONT, 12), dropdown_font=(FONT, 12))

    def segmented(self, key, values):
        return lambda parent: ctk.CTkSegmentedButton(
            parent, variable=self.v[key], values=values, height=36, corner_radius=10, fg_color=FIELD,
            selected_color=PURPLE, selected_hover_color=PURPLE_HOVER, unselected_color=FIELD,
            unselected_hover_color=CARD, text_color=TEXT, font=(FONT_MEDIUM, 12))

    def editable_choice(self, key, values):
        return lambda parent: ctk.CTkComboBox(
            parent, variable=self.v[key], values=values or [""], height=38, corner_radius=10, fg_color=FIELD,
            border_color=FIELD, button_color=PURPLE, button_hover_color=PURPLE_HOVER, dropdown_fg_color=CARD,
            dropdown_hover_color=FIELD, text_color=TEXT, font=(FONT, 12), dropdown_font=(FONT, 12))

    def textbox(self, text, height):
        def make(parent):
            box = ctk.CTkTextbox(parent, height=height, corner_radius=10, fg_color=FIELD, text_color=TEXT,
                                 font=(FONT, 12), wrap="word")
            box.insert("1.0", text)
            return box
        return make

    def tiles(self, card, var, options, columns=3, on_select=None):
        """Clickable cards with icon, name and tag. options: {value: (icon, name, tag or None)}"""
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.grid(row=card.next_row, column=0, sticky="ew", padx=12)
        card.next_row += 1
        for c in range(columns):
            grid.grid_columnconfigure(c, weight=1, uniform="tile")
        frames = {}

        def paint():
            for value, frame in frames.items():
                selected = var.get() == value
                frame.configure(border_color=PINK if selected else FIELD, fg_color="#3a2a4a" if selected else FIELD)

        def pick(value):
            var.set(value)
            paint()
            if on_select:
                on_select(value)

        for i, (value, (glyph, name, tag)) in enumerate(options.items()):
            frame = ctk.CTkFrame(grid, corner_radius=12, border_width=2, height=64)
            frame.grid(row=i // columns, column=i % columns, sticky="ew", padx=6, pady=6)
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text="", image=icon(glyph, 22, PINK)).grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=12)
            ctk.CTkLabel(frame, text=name, font=(FONT_SEMI, 12), text_color=TEXT, anchor="w", height=20).grid(
                row=0, column=1, sticky="sw", pady=(12, 0), padx=(0, 10))
            if tag:
                bg, fg = TAGS[tag]
                ctk.CTkLabel(frame, text=f" {tag} ", font=(FONT_SEMI, 9), fg_color=bg, text_color=fg,
                             corner_radius=6, height=18).grid(row=1, column=1, sticky="nw", pady=(3, 12))
            else:
                frame.grid_rowconfigure(1, minsize=12)
            bind_click(frame, lambda v=value: pick(v))
            frames[value] = frame
        paint()
        return paint

    def switch(self, card, key, glyph, title, text):
        row = ctk.CTkFrame(card, fg_color=FIELD, corner_radius=12)
        row.grid(row=card.next_row, column=0, sticky="ew", padx=18, pady=5)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="", image=icon(glyph, 22, PINK)).grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=12)
        ctk.CTkLabel(row, text=title, font=(FONT_SEMI, 12), text_color=TEXT, anchor="w", height=20).grid(
            row=0, column=1, sticky="sw", pady=(12, 0))
        ctk.CTkLabel(row, text=text, font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
                     wraplength=480, height=18).grid(row=1, column=1, sticky="nw", pady=(0, 12))
        ctk.CTkSwitch(row, text="", variable=self.v[key], onvalue="1", offvalue="0", progress_color=PINK,
                      width=50).grid(row=0, column=2, rowspan=2, padx=14)
        card.next_row += 1

    def actions(self, card, buttons):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=card.next_row, column=0, sticky="w", padx=18, pady=(14, 0))
        for i, (glyph, text, command) in enumerate(buttons):
            primary = i == len(buttons) - 1
            ctk.CTkButton(row, text=f" {text}", image=icon(glyph, 15, "white"), compound="left", command=command,
                          height=36, corner_radius=10, font=(FONT_MEDIUM, 12), fg_color=PINK if primary else PURPLE,
                          hover_color=PINK_HOVER if primary else PURPLE_HOVER).pack(side="left", padx=(0, 8))
        status = ctk.CTkLabel(card, text="", font=(FONT, 11), justify="left", wraplength=640, compound="left")
        status.grid(row=card.next_row + 1, column=0, sticky="w", padx=18, pady=(8, 0))
        card.next_row += 2
        return status

    def background(self, label, work, ok):
        label.configure(text="  Pera, tô testando...", text_color=MUTED, image=icon(I_GEAR, 14, MUTED))

        def run():
            try:
                result = work()
                self.after(0, lambda: label.configure(text="  " + ok(result), text_color=OK, image=icon(I_CHECK, 14, OK)))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: label.configure(text="  Ih, deu ruim: " + msg[:300], text_color=ERROR,
                                                      image=icon(I_ALERT, 14, ERROR)))
        threading.Thread(target=run, daemon=True).start()

    # --- pages -------------------------------------------------------------------
    def page_identity(self, p):
        c = self.card(p, 2, I_CARD, "Nome")
        self.field(c, "Nome da assistente", self.entry("ASSISTANT_NAME"),
                   "É o nome que você vai chamar: \"Ei, <nome>\". Curto e diferente funciona melhor.")
        self.field(c, "Outras grafias do nome", self.entry("WAKE_ALIASES"),
                   "Opcional, separadas por vírgula. Se ela não atender, veja na janela preta como o nome foi "
                   "escrito e coloque aqui.")
        self.end(c)

        c = self.card(p, 3, I_PERSON, "Você")
        self.field(c, "Seu nome", self.entry("USER_NAME"))
        self.field(c, "Falar de você como", self.segmented("USER_PRONOUN", persona.PRONOUNS))
        self.facts = self.field(c, "Coisas sobre você", self.textbox(read_file("sobre-voce.txt").strip(), 80),
                                "Opcional: gostos, jogos, apelidos, o que ela deve lembrar sempre.")
        self.end(c)

        c = self.card(p, 4, I_SMILE, "Personalidade")
        self.label(c, "Estilo")
        self.tiles(c, self.v["PERSONALITY"], {k: (g, k, None) for k, g in PERSONALITY_TILES.items()},
                   on_select=self.on_preset)
        self.personality = self.field(c, "Descrição", self.textbox(
            read_file("personalidade.txt").strip() or persona.PERSONALITIES.get(self.v["PERSONALITY"].get(), ""), 100),
            "Escreva do seu jeito. Se mudar o texto, ele vira uma personalidade personalizada.")
        self.end(c)

    def on_preset(self, choice):
        text = persona.PERSONALITIES.get(choice)
        if text:
            self.personality.delete("1.0", "end")
            self.personality.insert("1.0", text)

    def page_brain(self, p):
        c = self.card(p, 2, I_ROBOT, "Serviço de IA")
        self.label(c, "Qual IA usar")
        self.tiles(c, self.v["BRAIN"], BRAIN_TILES, on_select=lambda _: on_change())
        hint = self.hint(c, "", PURPLE)
        key = self.field(c, "Chave de API", self.entry("BRAIN_API_KEY", secret=True),
                         "É como uma senha: não mande pra ninguém. O guia mostra onde pegar.")
        model = self.field(c, "Modelo", self.editable_choice("BRAIN_MODEL", []),
                           "Vazio = o sugerido. \"Listar modelos\" mostra as opções da sua conta.")
        status = self.actions(c, [
            (I_LIST, "Listar modelos", lambda: self.background(
                status, lambda: list_models(self.v["BRAIN"].get(), self.v["BRAIN_API_KEY"].get(),
                                            self.v["BRAIN_BASE_URL"].get() or None), fill_models)),
            (I_BOLT, "Testar conexão", lambda: self.background(
                status, lambda: quick_test(self.v["BRAIN"].get(), self.v["BRAIN_API_KEY"].get(),
                                           self.v["BRAIN_MODEL"].get() or None, self.v["BRAIN_BASE_URL"].get() or None),
                lambda text: f"Funcionou! Ela respondeu: {text}")),
        ])
        self.end(c)

        c = self.card(p, 3, I_GEAR, "Avançado")
        self.field(c, "Endereço da API", self.entry("BRAIN_BASE_URL"),
                   "Só para \"Outro\" ou servidor próprio. Vazio = padrão.")
        self.field(c, "Esforço (só Claude)", self.segmented("CLAUDE_EFFORT", EFFORTS),
                   "low responde mais rápido; high pensa mais.")
        self.end(c)

        def fill_models(ids):
            model.configure(values=ids or [""])
            return f"{len(ids)} modelos encontrados: abra a lista do campo Modelo."

        def on_change():
            prov = self.v["BRAIN"].get()
            model.configure(values=[PROVIDERS[prov][3]] if PROVIDERS[prov][3] else [""])
            key.configure(state="disabled" if prov in ("claude-code", "ollama") else "normal",
                          fg_color=CARD if prov in ("claude-code", "ollama") else FIELD)
            hint.configure(text=BRAIN_HINTS.get(prov, ""))
        on_change()

    def page_voice(self, p):
        from assistente.voice import EDGE_VOICES
        c = self.card(p, 2, I_SPEAKER, "Serviço de voz")
        self.tiles(c, self.v["VOICE"], VOICE_TILES, columns=4, on_select=lambda _: on_change())
        key = self.field(c, "Chave de API", self.entry("VOICE_API_KEY", secret=True))
        voice = self.field(c, "Voz", self.editable_choice("VOICE_ID", EDGE_VOICES),
                           "Edge: escolha da lista. Fish/ElevenLabs: cole o ID da voz que você escolheu no site.")
        self.field(c, "Modelo da voz (opcional)", self.entry("VOICE_MODEL"))

        def test():
            from assistente.voice import make_synth, play
            synth = make_synth(self.v["VOICE"].get(), self.v["VOICE_API_KEY"].get(), self.v["VOICE_ID"].get(),
                               self.v["VOICE_MODEL"].get() or None)
            if not synth:
                raise RuntimeError("falta a chave ou o ID da voz")
            name = self.v["ASSISTANT_NAME"].get() or "sua assistente"
            play(synth(f"Oi! Eu sou {name}. Essa vai ser a minha voz."))
        status = self.actions(c, [(I_PLAY, "Ouvir a voz", lambda: self.background(
            status, test, lambda _: "Tocou! Se não ouviu nada, a culpa é da saída de som do Windows, não minha."))])
        self.end(c)

        def on_change():
            prov = self.v["VOICE"].get()
            paid = prov in ("fish", "elevenlabs")
            key.configure(state="normal" if paid else "disabled", fg_color=FIELD if paid else CARD)
            voice.configure(values=EDGE_VOICES if prov == "edge" else [""])
        on_change()

    def page_mic(self, p):
        c = self.card(p, 2, I_MIC, "Entrada de áudio")
        self.field(c, "Microfone", self.choice(self.v["MIC_DEVICE"], [""] + mic_names()),
                   "Vazio = o microfone padrão do Windows.")
        self.field(c, "Precisão do reconhecimento", self.segmented("WHISPER_MODEL", WHISPER),
                   "large-v3-turbo é o melhor, mas precisa de placa NVIDIA. Sem ela o programa usa o 'small' sozinho.")
        self.field(c, "Segundos ouvindo depois de responder", self.entry("FOLLOWUP_SECONDS"),
                   "Nesse tempo dá pra continuar a conversa sem dizer o nome de novo.")
        self.end(c)

    def page_extras(self, p):
        c = self.card(p, 2, I_AVATAR, "Avatar (VTube Studio)")
        self.switch(c, "VTUBE_STUDIO", I_AVATAR, "Usar avatar do VTube Studio", "A boca mexe com a voz e o rosto muda com a emoção.")
        self.switch(c, "OVERLAY", I_SPARKLE, "Avatar flutuante", "Mostra o avatar no canto da tela, por cima das janelas.")
        self.field(c, "Tamanho do avatar flutuante", self.entry("OVERLAY_SCALE"),
                   "0.5 = metade do tamanho da janela do VTube Studio.")
        self.end(c)
        c = self.card(p, 3, I_PUZZLE, "Outros recursos")
        self.switch(c, "WATCH", I_VIDEO, "Comentar vídeos do YouTube", "Envia prints da tela para a IA de vez em quando.")
        self.field(c, "Minutos sem uso até reiniciar a conversa", self.entry("IDLE_RESET_MINUTES"),
                   "Começar do zero de vez em quando economiza a sua cota da IA.")
        self.end(c)

    # --- saving ------------------------------------------------------------------
    def save(self):
        name = self.v["ASSISTANT_NAME"].get().strip()
        if not name:
            self.show("identity")
            messagebox.showwarning("Calma aí", "Ela precisa de um nome, né? Preenche lá em Identidade.")
            return False
        if not self.step_done("brain"):
            self.show("brain")
            messagebox.showwarning("Calma aí", "Sem chave de API ela não pensa. Cola a chave lá em Inteligência.")
            return False
        personality = self.personality.get("1.0", "end").strip()
        facts = self.facts.get("1.0", "end").strip()
        preset = persona.PERSONALITIES.get(self.v["PERSONALITY"].get())
        if preset is not None and personality != preset:
            self.v["PERSONALITY"].set(CUSTOM)
        with open(ENV, "w", encoding="utf-8") as fh:
            fh.write("# Gerado pelo configurador do Maid Me. Pode editar aqui ou abrir o configurador de novo.\n")
            for k in DEFAULTS:
                fh.write(f"{k}={self.v[k].get().strip()}\n")
        for file, text in [("personalidade.txt", personality), ("sobre-voce.txt", facts),
                           ("persona.md", persona.build(name, self.v["USER_NAME"].get(),
                                                        self.v["USER_PRONOUN"].get(), personality, facts))]:
            with open(os.path.join(ROOT, file), "w", encoding="utf-8") as fh:
                fh.write(text)
        return True

    def save_and_start(self):
        if self.save():
            subprocess.Popen(["cmd", "/c", "start", "", os.path.join(ROOT, "Iniciar.bat")], cwd=ROOT)
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
