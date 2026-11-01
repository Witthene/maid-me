"""Set up the assistant from inside the app: identity, brain, voice and which features are on.

Opened from the profile panel (and by itself on the first run). Saves .env and builds persona.md;
personalidade.txt and sobre-voce.txt keep the free text. Each service has a button that opens the
exact page where the key (or the download) is, so nobody has to hunt for it.
"""
import os
import sys
import threading
import webbrowser

from dotenv import dotenv_values
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from assistente import persona, plat, sfx
from assistente.brain import PROVIDERS, list_models, quick_test
from assistente.sigil_studio import CyberFrame, NeonButton, style

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
CUSTOM = "Personalizada"
GENDERS = {"feminino": "Feminino", "masculino": "Masculino", "neutro": "Neutro"}
PRONOUNS = {"ele": "Ele", "ela": "Ela", "neutro": "Neutro"}
DEFAULTS = {"ASSISTANT_NAME": "", "ASSISTANT_GENDER": "feminino", "WAKE_ALIASES": "", "USER_NAME": "",
            "USER_PRONOUN": "neutro", "PERSONALITY": "Sarcástica e engraçada", "BRAIN": "gemini", "BRAIN_API_KEY": "",
            "BRAIN_MODEL": "", "BRAIN_BASE_URL": "", "CLAUDE_EFFORT": "low", "VOICE": "edge", "VOICE_API_KEY": "",
            "VOICE_ID": "pt-BR-FranciscaNeural", "VOICE_MODEL": "", "MIC_DEVICE": "", "WHISPER_MODEL": "large-v3-turbo",
            "VTUBE_STUDIO": "0", "OVERLAY": "0", "OVERLAY_SCALE": "0.5", "WATCH": "0", "NETRUN": "1", "MEMORY": "1", "LOOK": "1",
            "FOLLOWUP_SECONDS": "20", "IDLE_RESET_MINUTES": "10"}

# where to get each key (or what to download), opened by the "Pegar a chave" button
BRAINS = {  # provider: label, needs key, link label, link, hint
    "gemini": ("Google Gemini · grátis", True, "Pegar a chave grátis",
               "https://aistudio.google.com/app/apikey", "Entre com sua conta Google, clique em Create API key e cole aqui."),
    "groq": ("Groq · grátis", True, "Pegar a chave grátis", "https://console.groq.com/keys",
             "Crie a conta, clique em Create API Key e cole aqui."),
    "openrouter": ("OpenRouter · tem modelos grátis", True, "Pegar a chave", "https://openrouter.ai/keys",
                   "Uma chave só pra vários modelos. Os que terminam em :free não cobram."),
    "claude-code": ("Claude · assinatura Pro/Max", False, "Instalar o Claude Code",
                    "https://claude.com/product/claude-code",
                    "Sem chave: instale o Claude Code, rode claude uma vez no terminal e faça login."),
    "anthropic": ("Claude · chave de API (pago)", True, "Pegar a chave", "https://console.anthropic.com/settings/keys",
                  "Precisa ter créditos na conta da Anthropic."),
    "openai": ("ChatGPT · chave de API (pago)", True, "Pegar a chave", "https://platform.openai.com/api-keys",
               "Precisa ter créditos na conta da OpenAI."),
    "deepseek": ("DeepSeek · pago e barato", True, "Pegar a chave", "https://platform.deepseek.com/api_keys", ""),
    "ollama": ("Ollama · roda no seu PC", False, "Baixar o Ollama", "https://ollama.com/download",
               "Sem chave e sem internet. Depois de instalar, baixe um modelo que use ferramentas, como qwen3."),
    "custom": ("Outro compatível com OpenAI", True, "", "", "Preencha também o endereço da API."),
}
VOICES = {  # provider: label, needs key, key link, voice list link, hint
    "edge": ("Microsoft Edge · grátis", False, "", "", "Não precisa de nada: escolha uma voz da lista."),
    "fish": ("Fish Audio · vozes de personagem", True, "https://fish.audio/app/api-keys/", "https://fish.audio/discovery/",
             "Escolha uma voz no site e copie o ID dela (fica no endereço da página da voz)."),
    "elevenlabs": ("ElevenLabs · vozes realistas", True, "https://elevenlabs.io/app/settings/api-keys",
                   "https://elevenlabs.io/app/voice-library", "Escolha uma voz na biblioteca e copie o Voice ID."),
    "none": ("Sem voz · só texto", False, "", "", "Ela responde só escrevendo na janela."),
}
TAGS = {"token": ("TOKENS", "#ffb86b"), "gpu": ("PLACA", "#ff6b9a"), "cpu": ("PROCESSADOR", "#7dd3fc")}
FEATURES = (  # env key, title, cost tags, description
    ("LOOK", "Olhar a tela quando você fala dela", ("token",),
     "\"Olha isso\", \"tá vendo?\": manda um print junto. Cada print custa bem mais que uma frase."),
    ("MEMORY", "Lembrar das conversas", ("token",),
     "Guarda as últimas falas neste PC; cada conversa nova começa com um resumo delas."),
    ("NETRUN", "Efeitos de hacker", ("cpu",), "Painel neon no canto e a marca piscando na janela que ela mexe."),
    ("WATCH", "Comentar o que você faz", ("cpu",),
     "Espia a tela de vez em quando e solta um comentário. Não gasta tokens: usa a visão local ou frases prontas."),
    ("VTUBE_STUDIO", "Avatar do VTube Studio", ("gpu", "cpu"), "A boca mexe com a voz e o rosto muda com a emoção."),
    ("OVERLAY", "Avatar flutuante", ("gpu", "cpu"), "Captura o VTube Studio o tempo todo pra mostrar o avatar por cima de tudo."),
)


def tags_html(tags):
    return "&nbsp;".join(f"<span style='color:{TAGS[t][1]}; font-size:10px; font-weight:600'>[{TAGS[t][0]}]</span>"
                         for t in tags)
WHISPER = {"large-v3-turbo": "Máxima (uns 2 GB da placa NVIDIA)", "medium": "Alta (1,5 GB)", "small": "Média (leve)",
           "base": "Básica (bem leve)"}


def read_file(name):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def configured():
    return os.path.exists(ENV) and os.path.exists(os.path.join(ROOT, "persona.md"))


def mic_names():
    try:
        import sounddevice as sd
        apis = sd.query_hostapis()
        return [d["name"] for d in sd.query_devices()
                if d["max_input_channels"] > 0 and (not plat.WINDOWS or "WASAPI" in apis[d["hostapi"]]["name"])]
    except Exception:
        return []


class _Done(QObject):
    signal = Signal(object, str, bool)  # label, text, ok


class SettingsDialog(QDialog):
    def __init__(self, win, accent, first_run=False):
        super().__init__(win, Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.accent, self.first_run = accent, first_run
        self.cfg = {**DEFAULTS, **{k: v for k, v in dotenv_values(ENV).items() if v is not None}}
        self.done_sig = _Done()
        self.done_sig.signal.connect(lambda label, text, ok: label.setText(
            f"<span style='color:{'#7ee0a6' if ok else '#ff7a8a'}'>{text}</span>"))
        self.setStyleSheet(style(accent) + f"""
            QPlainTextEdit {{ background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,40);
                              border-radius: 10px; padding: 4px 8px; }}
            QPlainTextEdit:focus {{ border: 1px solid {accent.name()}; }}
            QScrollArea {{ border: none; }}
            QScrollBar:vertical {{ width: 6px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,50); border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QLabel#hint {{ color: rgba(240,228,250,150); font-size: 11px; }}
        """)
        title = "Bem-vindo! Vamos criar sua assistente" if first_run else "Configurar assistente"
        self.frame = CyberFrame(accent, title, self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(18, 42, 18, 16)
        lay.setSpacing(10)

        tabs = QHBoxLayout()
        self.stack = QStackedWidget()
        self.tab_buttons = []
        for i, (name, build) in enumerate((("Identidade", self.page_identity), ("Inteligência", self.page_brain),
                                           ("Voz e ouvido", self.page_voice), ("Recursos", self.page_features))):
            b = NeonButton(name, accent)
            b.clicked.connect(lambda _=False, i=i: self.show_page(i))
            tabs.addWidget(b)
            self.tab_buttons.append(b)
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            page = QWidget(); col = QVBoxLayout(page); col.setSpacing(6); col.setContentsMargins(2, 4, 8, 4)
            build(col)
            col.addStretch(1)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        lay.addLayout(tabs)
        lay.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        self.status = QLabel(""); self.status.setObjectName("hint"); self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        if not first_run:
            b = NeonButton("Cancelar", accent, danger=True); b.clicked.connect(self.reject); foot.addWidget(b)
        b = NeonButton("Salvar e começar" if first_run else "Salvar e reiniciar", accent)
        b.clicked.connect(self.save); foot.addWidget(b)
        lay.addLayout(foot)
        self.resize(560, 620)
        self.show_page(0)
        sfx.play("open")

    # --- building blocks -------------------------------------------------------------
    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        for k, b in enumerate(self.tab_buttons):
            b.accent = self.accent if k == i else self.accent.darker(250)
            b.update()

    def section(self, col, text):
        lab = QLabel(text.upper()); lab.setObjectName("section")
        col.addSpacing(6)
        col.addWidget(lab)

    def hint(self, col, text=""):
        lab = QLabel(text); lab.setObjectName("hint"); lab.setWordWrap(True); lab.setOpenExternalLinks(True)
        col.addWidget(lab)
        return lab

    def line(self, col, label, key, secret=False, placeholder=""):
        if label:
            col.addWidget(QLabel(label))
        w = QLineEdit(self.cfg.get(key, "")); w.setPlaceholderText(placeholder)
        if secret:
            w.setEchoMode(QLineEdit.Password)
        col.addWidget(w)
        return w

    def combo(self, col, label, options, current, editable=False):
        """options: {value: label}. The value goes to .env, the label is what the user sees."""
        if label:
            col.addWidget(QLabel(label))
        w = QComboBox(); w.setEditable(editable)
        for value, text in options.items():
            w.addItem(text, value)
        i = w.findData(current)
        if i >= 0:
            w.setCurrentIndex(i)
        elif editable:
            w.setEditText(current)
        col.addWidget(w)
        return w

    @staticmethod
    def value(combo):
        if combo.isEditable():
            i = combo.findText(combo.currentText())
            return combo.itemData(i) if i >= 0 and combo.itemData(i) is not None else combo.currentText().strip()
        return combo.currentData()

    def buttons(self, col, items):
        row = QHBoxLayout()
        for text, fn in items:
            b = NeonButton(text, self.accent); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)

    def background(self, label, work, ok):
        label.setText("testando...")

        def run():
            try:
                self.done_sig.signal.emit(label, ok(work()), True)
            except Exception as e:
                self.done_sig.signal.emit(label, "Deu ruim: " + str(e)[:200], False)
        threading.Thread(target=run, daemon=True).start()

    # --- pages -------------------------------------------------------------------------
    def page_identity(self, col):
        self.section(col, "Assistente")
        self.name = self.line(col, "Nome", "ASSISTANT_NAME", placeholder="ex.: Luna")
        self.hint(col, "É como você vai chamar: \"Ei, Luna\". Curto e diferente funciona melhor.")
        self.gender = self.combo(col, "Gênero", GENDERS, self.cfg["ASSISTANT_GENDER"])
        self.aliases = self.line(col, "Outras grafias do nome (opcional)", "WAKE_ALIASES", placeholder="separadas por vírgula")
        self.section(col, "Você")
        self.user = self.line(col, "Seu nome", "USER_NAME")
        self.pronoun = self.combo(col, "Falar de você como", PRONOUNS, self.cfg["USER_PRONOUN"])
        col.addWidget(QLabel("Coisas sobre você (opcional)"))
        self.facts = QPlainTextEdit(read_file("sobre-voce.txt")); self.facts.setFixedHeight(64)
        self.facts.setPlaceholderText("gostos, jogos, apelidos, o que ela deve lembrar sempre")
        col.addWidget(self.facts)
        self.section(col, "Personalidade")
        presets = {k: k for k in persona.PERSONALITIES}
        presets[CUSTOM] = CUSTOM
        self.preset = self.combo(col, "Estilo", presets, self.cfg["PERSONALITY"])
        self.personality = QPlainTextEdit(read_file("personalidade.txt")
                                          or persona.PERSONALITIES.get(self.cfg["PERSONALITY"], ""))
        self.personality.setFixedHeight(80)
        col.addWidget(self.personality)
        self.preset.currentIndexChanged.connect(lambda: persona.PERSONALITIES.get(self.preset.currentData()) and
                                                self.personality.setPlainText(persona.PERSONALITIES[self.preset.currentData()]))

    def page_brain(self, col):
        self.section(col, "Quem vai pensar por ela")
        self.brain = self.combo(col, "", {k: v[0] for k, v in BRAINS.items()}, self.cfg["BRAIN"])
        self.brain_hint = self.hint(col)
        self.brain_link = NeonButton("", self.accent)
        self.brain_link.clicked.connect(lambda: webbrowser.open(BRAINS[self.brain.currentData()][3]))
        row = QHBoxLayout(); row.addWidget(self.brain_link); row.addStretch(1); col.addLayout(row)
        self.key_label = QLabel("Chave de API"); col.addWidget(self.key_label)
        self.key = QLineEdit(self.cfg["BRAIN_API_KEY"]); self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("cole a chave aqui"); col.addWidget(self.key)
        self.model = self.combo(col, "Modelo (vazio = o sugerido)", {}, self.cfg["BRAIN_MODEL"], editable=True)
        status = QLabel(""); status.setObjectName("hint"); status.setWordWrap(True)

        def fill(ids):
            current = self.model.currentText()
            self.model.clear()
            for i in ids:
                self.model.addItem(i, i)
            self.model.setEditText(current)
            return f"{len(ids)} modelos encontrados: abra a lista do campo Modelo."
        self.buttons(col, [
            ("Testar", lambda: self.background(status, lambda: quick_test(
                self.brain.currentData(), self.key.text().strip(), self.value(self.model) or None,
                self.base_url.text().strip() or None), lambda t: f"Funcionou! Resposta: {t[:120]}")),
            ("Listar modelos", lambda: self.background(status, lambda: list_models(
                self.brain.currentData(), self.key.text().strip(), self.base_url.text().strip() or None), fill)),
        ])
        col.addWidget(status)
        self.section(col, "Avançado")
        self.base_url = self.line(col, "Endereço da API (só pra \"Outro\")", "BRAIN_BASE_URL")
        self.effort = self.combo(col, "Esforço do Claude", {"low": "Rápido (menos tokens)", "medium": "Médio",
                                                            "high": "Pensa mais (mais tokens)"},
                                 self.cfg["CLAUDE_EFFORT"])
        self.brain.currentIndexChanged.connect(self.on_brain)
        self.on_brain()

    def on_brain(self):
        prov = self.brain.currentData()
        _, needs_key, link_text, link, hint = BRAINS[prov]
        self.brain_hint.setText(hint)
        self.brain_link.setText(link_text)
        self.brain_link.setVisible(bool(link))
        self.brain_link.setFixedWidth(self.brain_link.sizeHint().width())
        self.key.setEnabled(needs_key)
        self.key_label.setEnabled(needs_key)
        self.model.lineEdit().setPlaceholderText(PROVIDERS[prov][3] or "o padrão do serviço")

    def page_voice(self, col):
        from assistente.voice import EDGE_VOICES
        self.section(col, "Voz")
        self.voice = self.combo(col, "", {k: v[0] for k, v in VOICES.items()}, self.cfg["VOICE"])
        self.voice_hint = self.hint(col)
        row = QHBoxLayout()
        self.voice_key_link = NeonButton("Pegar a chave", self.accent)
        self.voice_key_link.clicked.connect(lambda: webbrowser.open(VOICES[self.voice.currentData()][2]))
        self.voice_list_link = NeonButton("Escolher uma voz", self.accent)
        self.voice_list_link.clicked.connect(lambda: webbrowser.open(VOICES[self.voice.currentData()][3]))
        row.addWidget(self.voice_key_link); row.addWidget(self.voice_list_link); row.addStretch(1)
        col.addLayout(row)
        self.voice_key = self.line(col, "Chave de API", "VOICE_API_KEY", secret=True)
        self.voice_id = self.combo(col, "Voz (nome ou ID)", {v: v for v in EDGE_VOICES}, self.cfg["VOICE_ID"], editable=True)
        status = QLabel(""); status.setObjectName("hint")

        def test():
            from assistente.voice import make_synth, play
            synth = make_synth(self.voice.currentData(), self.voice_key.text().strip(), self.value(self.voice_id),
                               self.cfg.get("VOICE_MODEL") or None)
            if not synth:
                raise RuntimeError("falta a chave ou o ID da voz")
            play(synth(f"Oi! Eu sou {self.name.text().strip() or 'sua assistente'}. Essa vai ser a minha voz."))
            return "Tocou!"
        self.buttons(col, [("Ouvir a voz", lambda: self.background(status, test, lambda r: r))])
        col.addWidget(status)
        self.section(col, "Ouvido")
        self.mic = self.combo(col, "Microfone", {"": "Padrão do Windows", **{m: m for m in mic_names()}}, self.cfg["MIC_DEVICE"])
        self.whisper = self.combo(col, "Precisão pra te entender", WHISPER, self.cfg["WHISPER_MODEL"])
        self.hint(col, "Sem placa NVIDIA ela usa sozinha a versão leve, só fica um pouco mais lenta.")
        self.voice.currentIndexChanged.connect(self.on_voice)
        self.on_voice()

    def on_voice(self):
        from assistente.voice import EDGE_VOICES
        prov = self.voice.currentData()
        _, needs_key, key_link, list_link, hint = VOICES[prov]
        self.voice_hint.setText(hint)
        self.voice_key.setEnabled(needs_key)
        self.voice_key_link.setVisible(bool(key_link))
        self.voice_list_link.setVisible(bool(list_link))
        self.voice_id.setEnabled(prov != "none")
        current = self.voice_id.currentText()
        self.voice_id.clear()
        if prov == "edge":
            for v in EDGE_VOICES:
                self.voice_id.addItem(v, v)
        self.voice_id.setEditText(current if (prov == "edge") == (current in EDGE_VOICES) else
                                  (EDGE_VOICES[0] if prov == "edge" else ""))

    def page_features(self, col):
        self.section(col, "Ligue só o que quiser")
        legend = QLabel("  ".join(tags_html((k,)) + f" <span style='font-size:10px'>{t}</span>" for k, t in
                                  (("token", "gasta da sua cota da IA"), ("gpu", "usa a placa de vídeo"),
                                   ("cpu", "usa o processador"))))
        legend.setWordWrap(True)
        col.addWidget(legend)
        self.switches = {}
        for key, title, tags, text in FEATURES:
            row = QHBoxLayout()
            box = QCheckBox(title); box.setChecked(self.cfg.get(key, "0") != "0")
            row.addWidget(box); row.addStretch(1); row.addWidget(QLabel(tags_html(tags)))
            col.addLayout(row)
            self.hint(col, text)
            self.switches[key] = box
        row = QHBoxLayout()
        settings = getattr(self.parent(), "settings", None) or {}
        self.eyes = QCheckBox("Visão local (Ollama)"); self.eyes.setChecked(settings.get("eyes_on", True))
        row.addWidget(self.eyes); row.addStretch(1); row.addWidget(QLabel(tags_html(("gpu",))))
        col.addLayout(row)
        self.hint(col, "Se o Ollama estiver instalado, usa uns 4 GB da placa pros comentários. Desligada, eles viram "
                       "frases prontas.")
        self.section(col, "Conversa")
        self.followup = self.combo(col, "Depois de responder, continuar ouvindo sem o nome por",
                                   {"0": "Nada", "10": "10 segundos", "20": "20 segundos", "40": "40 segundos"},
                                   self.cfg["FOLLOWUP_SECONDS"])
        self.idle = self.combo(col, "Começar conversa nova depois de parada por",
                               {"5": "5 minutos", "10": "10 minutos", "30": "30 minutos", "120": "2 horas"},
                               self.cfg["IDLE_RESET_MINUTES"])
        self.hint(col, "Conversa longa reenvia o histórico todo a cada fala: zerar antes economiza a cota da IA.")
        self.section(col, "Privacidade")
        self.hint(col, "Suas chaves ficam só neste computador, no arquivo .env da pasta do programa. Nada vai pra "
                       "lugar nenhum além do serviço de IA e de voz que você escolheu.")

    # --- saving ----------------------------------------------------------------------
    def save(self):
        name = self.name.text().strip()
        prov = self.brain.currentData()
        if not name:
            self.show_page(0)
            self.status.setText("<span style='color:#ff7a8a'>Ela precisa de um nome, né?</span>")
            return
        if BRAINS[prov][1] and not self.key.text().strip():
            self.show_page(1)
            self.status.setText("<span style='color:#ff7a8a'>Falta a chave: clique no botão de pegar a chave e cole aqui.</span>")
            return
        personality = self.personality.toPlainText().strip()
        preset = self.preset.currentData()
        if persona.PERSONALITIES.get(preset) not in (None, personality):
            preset = CUSTOM
        facts = self.facts.toPlainText().strip()
        values = dict(self.cfg)
        values.update({
            "ASSISTANT_NAME": name, "ASSISTANT_GENDER": self.gender.currentData(),
            "WAKE_ALIASES": self.aliases.text().strip(), "USER_NAME": self.user.text().strip(),
            "USER_PRONOUN": self.pronoun.currentData(), "PERSONALITY": preset, "BRAIN": prov,
            "BRAIN_API_KEY": self.key.text().strip(), "BRAIN_MODEL": self.value(self.model) or "",
            "BRAIN_BASE_URL": self.base_url.text().strip(), "CLAUDE_EFFORT": self.effort.currentData(),
            "VOICE": self.voice.currentData(), "VOICE_API_KEY": self.voice_key.text().strip(),
            "VOICE_ID": self.value(self.voice_id) or "", "MIC_DEVICE": self.mic.currentData() or "",
            "WHISPER_MODEL": self.whisper.currentData(), "FOLLOWUP_SECONDS": self.followup.currentData(),
            "IDLE_RESET_MINUTES": self.idle.currentData(),
            **{k: "1" if b.isChecked() else "0" for k, b in self.switches.items()},
        })
        win = self.parent()
        if hasattr(win, "settings"):
            from assistente import eyes
            win.settings["eyes_on"] = self.eyes.isChecked()
            win.save_settings()
            eyes.set_enabled(self.eyes.isChecked())
        with open(ENV, "w", encoding="utf-8") as f:
            f.write("# Configuração do Maid Me. Mude pelo app: clique na foto da assistente > Configurar.\n")
            for k in DEFAULTS:
                f.write(f"{k}={values.get(k, '')}\n")
        files = (("personalidade.txt", personality), ("sobre-voce.txt", facts),
                 ("persona.md", persona.build(name, values["USER_NAME"], values["USER_PRONOUN"], personality, facts,
                                              gender=values["ASSISTANT_GENDER"])))
        for file, text in files:
            with open(os.path.join(ROOT, file), "w", encoding="utf-8") as f:
                f.write(text)
        self.accept()


def restart():
    """Start a fresh copy of the app (new settings load on start) and leave."""
    import subprocess
    from PySide6.QtWidgets import QApplication
    subprocess.Popen([plat.pythonw(), "-m", "assistente"], cwd=ROOT)
    QApplication.quit()
    os._exit(0)
