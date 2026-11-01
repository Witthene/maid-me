"""Everything that depends on the operating system, for Windows and Linux.

Windows talks to user32 directly. Linux uses the usual X11 tools (xdotool, xprop, xprintidle,
paplay/aplay, xdg-open) when they are installed; on Wayland or without them the features that
need the focused window (marks over windows, comments about the program in use) quietly turn off.
"""
import os
import re
import shutil
import subprocess
import sys

WINDOWS = sys.platform == "win32"
SHELL = "PowerShell" if WINDOWS else "Bash"  # the command tool the brain gets

if WINDOWS:
    import ctypes
    import winsound
    from ctypes import wintypes as wt
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32


def _run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _has(tool):
    return shutil.which(tool) is not None


# --- startup ----------------------------------------------------------------------
def dpi_aware():
    if WINDOWS:
        user32.SetProcessDPIAware()


def app_id(name):
    """Own taskbar icon instead of Python's."""
    if WINDOWS:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(name)


def pythonw():
    """The interpreter to restart the app with, without a console window."""
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return exe if WINDOWS and os.path.exists(exe) else sys.executable


# --- files and sounds -------------------------------------------------------------
def open_path(path):
    if WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_wav(path):
    """Fire and forget, never blocking or cutting the voice."""
    if WINDOWS:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return
    player = next((p for p in ("paplay", "pw-play", "aplay") if _has(p)), None)
    if player:
        args = [player, "-q", path] if player == "aplay" else [player, path]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- our own windows --------------------------------------------------------------
def keep_on_top(widget):
    # HWND_TOPMOST again: other always-on-top windows (and games) can push us behind them.
    # On Linux the Qt flag WindowStaysOnTopHint is all the window manager listens to.
    if WINDOWS:
        user32.SetWindowPos(int(widget.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)


def hide_from_capture(widget):
    # WDA_EXCLUDEFROMCAPTURE: visible on screen, invisible to screenshots (Windows only)
    if WINDOWS:
        try:
            user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)
        except Exception:
            pass


# --- the window the user is using ---------------------------------------------------
class Window:
    """The focused window: id, process, title, program name and rect (physical pixels)."""

    def __init__(self, wid, pid, title, app, rect, desktop):
        self.id, self.pid, self.title, self.app, self.rect, self.desktop = wid, pid, title, app, rect, desktop


_X11 = not WINDOWS and os.getenv("XDG_SESSION_TYPE", "x11") != "wayland" and _has("xdotool")


def foreground():
    """The focused window, or None when it can't be known (Wayland, no xdotool...)."""
    if WINDOWS:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        desktop = (cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
                   or user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd))
        return Window(hwnd, pid.value, buf.value, _exe_name(pid.value), window_rect(hwnd), desktop)
    if not _X11:
        return None
    wid = _run("xdotool", "getactivewindow")
    if not wid.isdigit():
        return None
    wid = int(wid)
    pid = _run("xdotool", "getwindowpid", str(wid))
    cls = _run("xprop", "-id", str(wid), "WM_CLASS") if _has("xprop") else ""
    names = re.findall(r'"([^"]*)"', cls)
    app = (names[-1] if names else "").lower()
    kind = _run("xprop", "-id", str(wid), "_NET_WM_WINDOW_TYPE") if _has("xprop") else ""
    desktop = "DESKTOP" in kind or "DOCK" in kind
    return Window(wid, int(pid) if pid.isdigit() else 0, _run("xdotool", "getwindowname", str(wid)), app,
                  window_rect(wid), desktop)


def _exe_name(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return re.sub(r"\.exe$", "", buf.value.rsplit("\\", 1)[-1], flags=re.I).lower()
    finally:
        kernel32.CloseHandle(handle)


def window_rect(wid):
    """(left, top, right, bottom) of a window, or None if it's gone or minimized."""
    if WINDOWS:
        r = wt.RECT()
        if not user32.IsWindow(wid) or user32.IsIconic(wid) or not user32.GetWindowRect(wid, ctypes.byref(r)):
            return None
        return r.left, r.top, r.right, r.bottom
    out = _run("xdotool", "getwindowgeometry", "--shell", str(wid))
    v = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        x, y, w, h = (int(v[k]) for k in ("X", "Y", "WIDTH", "HEIGHT"))
    except (KeyError, ValueError):
        return None
    return x, y, x + w, y + h


def own_window_focused():
    w = foreground()
    return bool(w) and w.pid == os.getpid()


def focus_monitor():
    """(left, top, right, bottom) of the monitor the focused window is on, and the virtual desktop origin."""
    if not WINDOWS:
        return None, (0, 0)  # X11 grabs start at 0,0; the crop is done from the window center instead

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4),
                    ("rcWork", ctypes.c_long * 4), ("dwFlags", ctypes.c_ulong)]
    mon = user32.MonitorFromWindow(user32.GetForegroundWindow(), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    origin = (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))
    if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return None, origin
    return tuple(info.rcMonitor), origin


def idle_seconds():
    """Seconds since the last mouse or keyboard input (0 when it can't be known)."""
    if WINDOWS:
        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = _LastInput(ctypes.sizeof(_LastInput), 0)
        user32.GetLastInputInfo(ctypes.byref(info))
        return (kernel32.GetTickCount() - info.dwTime) / 1000
    ms = _run("xprintidle") if _has("xprintidle") else ""
    return int(ms) / 1000 if ms.isdigit() else 0
