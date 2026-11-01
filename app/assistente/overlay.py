"""Floating assistant: shows the VTube Studio model in a corner of the screen, on top of everything.

VTube Studio's window is captured (it can sit behind other windows, just not minimized),
its solid background color is keyed out, and the result is drawn into a transparent,
always-on-top window that mouse clicks pass through.
"""
import ctypes
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np
from windows_capture import WindowsCapture

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
ULW_ALPHA, AC_SRC_ALPHA = 2, 1
SW_SHOWNOACTIVATE = 4
SPI_GETWORKAREA = 0x30
KEY_SOFTNESS = (40, 90)  # color distance where the background starts / stops being see-through
MARGIN = 10

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def find_vts_window():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if proc:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                if buf.value.lower().endswith("vtube studio.exe"):
                    found.append(hwnd)
            kernel32.CloseHandle(proc)
        return not found

    user32.EnumWindows(check, 0)
    return found[0] if found else None


def key_out_background(bgra):
    """BGRA frame -> premultiplied BGRA with the solid background made transparent."""
    img = bgra[:, :, :3].astype(np.int16)
    key = np.median(np.stack([img[2, 2], img[2, -3], img[-3, 2], img[-3, -3]]), axis=0)
    dist = np.abs(img - key).max(axis=2)
    lo, hi = KEY_SOFTNESS
    alpha = np.clip((dist - lo) / (hi - lo), 0, 1)
    # pull the background tint out of semi-transparent edges
    dominant = int(np.argmax(key))
    if key[dominant] > 150 and alpha.min() < 1:
        others = [c for c in range(3) if c != dominant]
        limit = img[:, :, others].max(axis=2)
        edge = alpha < 1
        img[:, :, dominant][edge] = np.minimum(img[:, :, dominant], limit)[edge]
    out = np.empty(bgra.shape, dtype=np.uint8)
    out[:, :, :3] = (img * alpha[:, :, None]).astype(np.uint8)
    out[:, :, 3] = (alpha * 255).astype(np.uint8)
    return out


class Overlay:
    def __init__(self, scale=0.5):
        self._scale = scale
        self._frame = None
        self._capturing = False
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    # --- capture -------------------------------------------------------------
    def _start_capture(self):
        hwnd = find_vts_window()
        if not hwnd:
            return
        capture = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd,
                                 minimum_update_interval=33)

        @capture.event
        def on_frame_arrived(frame, control):
            small = cv2.resize(frame.frame_buffer, None, fx=self._scale, fy=self._scale,
                               interpolation=cv2.INTER_AREA)
            keyed = key_out_background(small)
            with self._lock:
                self._frame = keyed

        @capture.event
        def on_closed():
            self._capturing = False

        capture.start_free_threaded()
        self._capturing = True
        print("[corpo] avatar flutuando na tela")

    # --- window ----------------------------------------------------------------
    def _run(self):
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
        except Exception:
            pass
        hwnd = self._create_window()
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap, bits, size = None, None, (0, 0)
        last_try = 0
        msg = wintypes.MSG()
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if not self._capturing and time.time() - last_try > 3:
                last_try = time.time()
                try:
                    self._start_capture()
                except Exception as e:
                    print(f"[corpo] nao consegui capturar o VTube Studio: {e}")
            with self._lock:
                frame, self._frame = self._frame, None
            if frame is not None:
                h, w = frame.shape[:2]
                if (w, h) != size:
                    if bitmap:
                        gdi32.DeleteObject(bitmap)
                    bitmap, bits = self._make_bitmap(screen_dc, w, h)
                    gdi32.SelectObject(mem_dc, bitmap)
                    size = (w, h)
                ctypes.memmove(bits, np.ascontiguousarray(frame).ctypes.data, w * h * 4)
                self._present(hwnd, screen_dc, mem_dc, w, h)
            elif not self._capturing and size != (0, 0):
                user32.ShowWindow(hwnd, 0)  # hide while VTube Studio is closed
                size = (0, 0)
            time.sleep(1 / 60)

    def _create_window(self):
        self._wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=kernel32.GetModuleHandleW(None),
                       lpszClassName="MaidMeOverlay")
        user32.RegisterClassW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        return user32.CreateWindowExW(ex, "MaidMeOverlay", "Maid Me", WS_POPUP, 0, 0, 1, 1, None, None,
                                      wc.hInstance, None)

    def _make_bitmap(self, dc, w, h):
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        return bitmap, bits

    def _present(self, hwnd, screen_dc, mem_dc, w, h):
        area = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        pos = wintypes.POINT(area.right - w - MARGIN, area.bottom - h)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(pos), ctypes.byref(size), mem_dc,
                                   ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # stay topmost
