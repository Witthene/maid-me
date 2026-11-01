"""How the console (Yui's hub) looks: colors, time stamps and readable tool lines instead of raw dicts."""
import ctypes
import os
import sys
import threading
import time

NAME = os.getenv("ASSISTANT_NAME", "Assistente")  # set again by __main__ after loading .env
GENDER = os.getenv("ASSISTANT_GENDER", "feminino")


def g(fem, masc, neutral=None):
    """Pick the word that matches the assistant's gender."""
    return {"feminino": fem, "masculino": masc}.get(GENDER, neutral or masc)

sink = None  # the app window sets this to receive every event: sink(kind, *args)
in_call = threading.Event()  # set while he's "calling" her: only then the open mic is listened to
call_muted = threading.Event()  # mute button on the call screen: the call stays up but she stops listening

PINK, PURPLE, CYAN = (255, 95, 168), (167, 139, 250), (125, 211, 252)
GREEN, YELLOW, RED, GRAY, WHITE = (126, 224, 166), (250, 204, 21), (255, 122, 138), (128, 120, 145), (240, 236, 248)
EMOTIONS = {"feliz": ("♪", GREEN), "brava": ("×", RED), "triste": ("…", CYAN), "surpresa": ("!", YELLOW),
            "convencida": ("★", PINK), "neutra": ("·", GRAY)}


def _enable_colors():
    # turn on ANSI escape handling in the classic Windows console (Windows Terminal already has it)
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


_enable_colors()
COLOR = bool(sys.stdout and sys.stdout.isatty()) and not os.getenv("NO_COLOR")  # stdout is None under pythonw


def c(text, rgb, bold=False, dim=False):
    if not COLOR:
        return text
    style = ("1;" if bold else "") + ("2;" if dim else "")
    return f"\x1b[{style}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[0m"


def _now():
    return c(time.strftime("%H:%M"), GRAY, dim=True)


def _out(line):
    print(line, flush=True)


def _emit(kind, *args):
    if sink:
        try:
            sink(kind, *args)
        except Exception as e:
            print(f"[hub] {e}", flush=True)


def banner():
    if COLOR:
        sys.stdout.write(f"\x1b]0;{NAME}\x07")  # window/tab title
    _out("")
    _out("  " + c("✦ " + " ".join(NAME.upper()), PINK, bold=True) + c("  ·  assistente de voz", GRAY))
    _out("  " + c("─" * 34, PURPLE, dim=True))
    _out("")


def ready(name=None):
    name = name or NAME
    _emit("ready")
    _out(c(f"  ● {g('Pronta', 'Pronto', 'Tudo pronto')}. Diga \"Ei, {name}\" e o que você quer.", GREEN) + c("  Ctrl+C para sair.", GRAY))
    _out("")


def you(text):
    _emit("you", text)
    _out("")
    _out(f"{_now()}  {c('Você', PINK, bold=True)}  {c(text, WHITE)}")


def yui(text, emotion=None):
    _emit("yui", text, emotion)
    mark, color = EMOTIONS.get(emotion or "neutra", EMOTIONS["neutra"])
    _out(f"{_now()}  {c(NAME, PURPLE, bold=True)} {c(mark, color, bold=True)} {text}")


def tool(name, inp):
    _emit("tool", describe(name, inp or {}))
    _out("        " + c("› " + describe(name, inp or {}), CYAN))


def describe(name, inp):
    """One short Portuguese line for a tool call."""
    if name in ("PowerShell", "Bash"):
        desc = (inp.get("description") or "").strip().rstrip(".")
        return desc[0].upper() + desc[1:] if desc else "Rodando um comando"
    path = os.path.basename(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "Read":
        return f"Lendo {path}"
    if name in ("Write", "Edit", "NotebookEdit"):
        return f"Mexendo em {path}"
    if name == "WebSearch":
        return f"Pesquisando: {inp.get('query', '')}"
    if name == "WebFetch":
        from urllib.parse import urlparse
        return f"Abrindo {urlparse(inp.get('url', '')).netloc}"
    return f"Usando {name}"


def look():
    """She just took a look at his screen (the netrun panel shows an optical scan)."""
    _emit("look")


def note(text):
    """Small status line: waiting, ignored, interrupted..."""
    _emit("note", text)
    _out("        " + c("· " + text, GRAY, dim=True))


def done():
    _emit("done")
    _out("        " + c("✓ " + g("pronta", "pronto", "feito"), GREEN, dim=True))


def ask(question, name, inp):
    _emit("ask", question, describe(name, inp or {}), str((inp or {}).get("command") or (inp or {}).get("file_path") or ""))
    _out("")
    _out("        " + c("! Permissão: ", YELLOW, bold=True) + c(describe(name, inp or {}), YELLOW))
    detail = (inp or {}).get("command") or (inp or {}).get("file_path") or ""
    if detail:
        _out("          " + c(str(detail)[:300], GRAY, dim=True))


def answer(allowed):
    _emit("answer", allowed)
    _out("        " + (c("✓ autorizado", GREEN) if allowed else c("× negado", RED)))


def info(tag, text):
    _emit("info", tag, text)
    _out(f"  {c('●', PURPLE)} {c(tag, PURPLE, bold=True)}  {c(text, GRAY)}")


def error(tag, text):
    _emit("error", tag, text)
    _out(f"  {c('×', RED)} {c(tag, RED, bold=True)}  {c(text, RED)}")
