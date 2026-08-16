#!/usr/bin/env python3
"""Death Tracker — transparent always-on-top death counter for Windows.

Hotkeys: D = die, R = reset, H = settings, U = unlock/move, End = quit.
Author: electro · MIT
"""

from __future__ import annotations

import ctypes
import json
import re
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

# --- paths / win32 ---

def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DATA_FILE = _app_dir() / "deaths.json"
ERROR_LOG = _app_dir() / "death_tracker_error.txt"
HOTKEY_LOG = _app_dir() / "hotkey_status.txt"

# Magenta color key — bg gets keyed out so only text shows
TRANS = "#ff00ff"

DEFAULT_COUNTER_COLOR = "#f2ddd0"
DEFAULT_SUCK_COLOR = "#ff4d6d"
TEXT_FLASH = "#ffffff"

COLOR_PRESETS = [
    ("Cream", "#f2ddd0"),
    ("White", "#ffffff"),
    ("Yellow", "#ffe566"),
    ("Gold", "#e8c547"),
    ("Orange", "#ff9f43"),
    ("Lime", "#7dffa0"),
    ("Cyan", "#5ce1ff"),
    ("Sky", "#74b9ff"),
    ("Blue", "#4a6cff"),
    ("Purple", "#b388ff"),
    ("Pink", "#ff4d6d"),
    ("Red", "#ff3b3b"),
]

# settings UI colors
BG = "#121018"
PANEL = "#1c1824"
FG = "#f0e6d8"
MUTED = "#8a7a88"
ACCENT = "#c41e3a"
GOLD = "#e8c547"
BTN_BG = "#2a2433"
BTN_ACTIVE = "#3a3248"

HK_DIE, HK_RESET, HK_SETTINGS, HK_UNLOCK, HK_QUIT = 1, 2, 3, 4, 5
VK_D, VK_R, VK_H, VK_U, VK_END = 0x44, 0x52, 0x48, 0x55, 0x23
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400
# freeze/unfreeze hotkeys on the hotkey thread (so you can type D/R/etc.)
WM_DT_SET_FREEZE = WM_USER + 77

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
LWA_COLORKEY = 0x00000001
ERROR_ALREADY_EXISTS = 183

# Win11 DWM — kill thin border / rounded outline on the overlay
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMWA_TRANSITIONS_FORCEDISABLED = 3

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    dwmapi = ctypes.windll.dwmapi
except OSError:
    dwmapi = None

user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    wintypes.COLORREF,
    ctypes.c_byte,
    wintypes.DWORD,
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex_to_colorref(hex_color: str) -> int:
    """#RRGGBB → COLORREF 0x00BBGGRR"""
    r, g, b = _hex_to_rgb(hex_color)
    return r | (g << 8) | (b << 16)


def polish_overlay_window(widget: tk.Misc, key_hex: str = TRANS) -> None:
    """
    Color-key transparency + hide Win11 border.
    Does NOT rewrite WND styles (that was causing black boxes).
    """
    try:
        widget.configure(bg=key_hex)
    except tk.TclError:
        pass
    try:
        widget.wm_attributes("-transparentcolor", key_hex)
    except tk.TclError:
        pass

    try:
        hwnd = int(widget.winfo_id())
    except tk.TclError:
        return

    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_LAYERED | WS_EX_TOOLWINDOW) & ~0x20
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetLayeredWindowAttributes(
            hwnd, _hex_to_colorref(key_hex), 0, LWA_COLORKEY
        )
    except OSError:
        pass

    if not dwmapi:
        return
    try:
        hwnd_w = wintypes.HWND(hwnd)
        corner = ctypes.c_int(DWMWCP_DONOTROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd_w,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner),
            ctypes.sizeof(corner),
        )
        border = ctypes.c_uint(DWMWA_COLOR_NONE)
        dwmapi.DwmSetWindowAttribute(
            hwnd_w,
            DWMWA_BORDER_COLOR,
            ctypes.byref(border),
            ctypes.sizeof(border),
        )
        disable = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd_w,
            DWMWA_TRANSITIONS_FORCEDISABLED,
            ctypes.byref(disable),
            ctypes.sizeof(disable),
        )
    except OSError:
        pass

# --- save / load ---

def normalize_hex(color: str | None, fallback: str) -> str:
    """Return #RRGGBB, or fallback if invalid / equals chroma key."""
    if not color or not isinstance(color, str):
        return fallback
    c = color.strip()
    if not c.startswith("#"):
        c = "#" + c
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        return fallback
    c = c.lower()
    # Never allow the transparency key color as visible text
    if c == TRANS.lower():
        return fallback
    return c


def shade_hex(color: str, factor: float) -> str:
    """Darken (factor < 1) or lighten toward white (factor > 1, clamped)."""
    c = normalize_hex(color, DEFAULT_SUCK_COLOR).lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    if factor >= 1.0:
        t = min(1.0, factor - 1.0)
        r = int(r + (255 - r) * t)
        g = int(g + (255 - g) * t)
        b = int(b + (255 - b) * t)
    else:
        r = max(0, min(255, int(r * factor)))
        g = max(0, min(255, int(g * factor)))
        b = max(0, min(255, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def blend_hex(a: str, b: str, t: float) -> str:
    """Lerp color a→b; t=0 => a, t=1 => b."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = _hex_to_rgb(normalize_hex(a, DEFAULT_COUNTER_COLOR))
    br, bg, bb = _hex_to_rgb(normalize_hex(b, DEFAULT_COUNTER_COLOR))
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    bl = int(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def default_data() -> dict:
    return {
        "session_deaths": 0,
        "total_deaths": 0,
        "pos_x": None,
        "pos_y": None,
        # Locked by default so you don't drag it mid-game by accident.
        # Press U (or Settings → Position) when you want to move it.
        "locked": True,
        # When True, global hotkeys are unregistered so you can type D/R/H/U normally
        "hotkeys_frozen": False,
        "font_size": 28,
        "counter_color": DEFAULT_COUNTER_COLOR,
        "suck_color": DEFAULT_SUCK_COLOR,
        # Text after "+1" in the death popup (e.g. "I SUCK", "again", "oops")
        "suck_text": "I SUCK",
    }


def load_data() -> dict:
    base = default_data()
    if not DATA_FILE.exists():
        return base
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return base
        for key in base:
            if key in raw:
                base[key] = raw[key]
        base["session_deaths"] = max(0, int(base["session_deaths"]))
        base["total_deaths"] = max(0, int(base["total_deaths"]))
        base["font_size"] = max(14, min(72, int(base.get("font_size") or 28)))
        base["locked"] = bool(base.get("locked"))
        base["hotkeys_frozen"] = bool(base.get("hotkeys_frozen"))
        base["counter_color"] = normalize_hex(
            str(base.get("counter_color")), DEFAULT_COUNTER_COLOR
        )
        base["suck_color"] = normalize_hex(
            str(base.get("suck_color")), DEFAULT_SUCK_COLOR
        )
        # Empty string is allowed (popup is just "+1")
        if "suck_text" in raw:
            base["suck_text"] = str(raw.get("suck_text") or "").strip()[:40]
        else:
            base["suck_text"] = "I SUCK"
        return base
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return base


def save_data(data: dict) -> None:
    try:
        DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def measure_text_size(
    text: str, font_size: int, pad: int = 6
) -> tuple[int, int]:
    font = _load_ui_font(max(8, int(font_size)), bold=True)
    probe = Image.new("RGB", (1, 1))
    pdraw = ImageDraw.Draw(probe)
    x0, y0, x1, y1 = pdraw.textbbox((0, 0), text, font=font)
    return max(1, x1 - x0) + pad * 2, max(1, y1 - y0) + pad * 2


def _load_ui_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
        ]
    candidates += [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_keyed_text(
    text: str,
    font_size: int,
    fill_hex: str,
    key_hex: str = TRANS,
    pad: int = 6,
    min_size: tuple[int, int] | None = None,
    opacity: float = 1.0,
) -> tuple[ImageTk.PhotoImage, tuple[int, int]]:
    """
    Rasterize text on a pure chroma-key background, then snap every pixel to
    either pure fill or pure key. Removes the anti-alias fringe that looks
    like an outline when using transparentcolor.

    opacity (0..1) simulates a fade for the float animation by raising the
    alpha cutoff so the glyph thins out and disappears cleanly.

    min_size keeps the bitmap at least that large (centered) so the window
    doesn't resize and flash a box when the number gains a digit.
    """
    font = _load_ui_font(max(8, int(font_size)), bold=True)
    fill_rgb = _hex_to_rgb(normalize_hex(fill_hex, DEFAULT_COUNTER_COLOR))
    key_rgb = _hex_to_rgb(key_hex)
    opacity = max(0.0, min(1.0, float(opacity)))

    probe = Image.new("RGB", (1, 1), key_rgb)
    pdraw = ImageDraw.Draw(probe)
    x0, y0, x1, y1 = pdraw.textbbox((0, 0), text, font=font)
    tw, th = max(1, x1 - x0), max(1, y1 - y0)

    content_w, content_h = tw + pad * 2, th + pad * 2
    out_w, out_h = content_w, content_h
    if min_size is not None:
        out_w = max(out_w, int(min_size[0]))
        out_h = max(out_h, int(min_size[1]))

    layer = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ox = (out_w - content_w) // 2 + pad - x0
    oy = (out_h - content_h) // 2 + pad - y0
    alpha = int(round(255 * opacity))
    draw.text((ox, oy), text, font=font, fill=(*fill_rgb, alpha))

    # higher cutoff while fading = fewer leftover pixels
    cutoff = int(20 + (1.0 - opacity) * 200)

    img = Image.new("RGB", (out_w, out_h), key_rgb)
    spx = layer.load()
    dpx = img.load()
    for y in range(out_h):
        for x in range(out_w):
            r, g, b, a = spx[x, y]
            if a > cutoff:
                dpx[x, y] = fill_rgb
            else:
                dpx[x, y] = key_rgb

    return ImageTk.PhotoImage(img), img.size


# --- hotkeys (own thread so they work while a game is focused) ---

class HotkeyThread(threading.Thread):
    def __init__(self, app: "DeathTracker") -> None:
        super().__init__(daemon=True, name="DeathTrackerHotkeys")
        self.app = app
        self.thread_id: int | None = None
        self._ready = threading.Event()
        self._status: list[str] = []
        self._specs = (
            (HK_DIE, VK_D, "D = die"),
            (HK_RESET, VK_R, "R = reset"),
            (HK_SETTINGS, VK_H, "H = settings"),
            (HK_UNLOCK, VK_U, "U = unlock / move"),
            (HK_QUIT, VK_END, "End = quit"),
        )
        self._frozen = False

    def run(self) -> None:
        self.thread_id = int(kernel32.GetCurrentThreadId())
        # Start frozen if save says so. 
        want_frozen = bool(self.app.data.get("hotkeys_frozen"))
        if want_frozen:
            self._frozen = True
            self._status = ["FROZEN  (hotkeys off for typing)"]
        else:
            self._register_all()

        try:
            HOTKEY_LOG.write_text(
                "Hotkey registration:\n" + "\n".join(self._status) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        self._ready.set()

        handlers = {
            HK_DIE: self.app.die,
            HK_RESET: self.app.reset_session,
            HK_SETTINGS: self.app.open_settings,
            HK_UNLOCK: self.app.unlock_for_move,
            HK_QUIT: self.app.shutdown,
        }

        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.message == WM_DT_SET_FREEZE:
                # wParam: 1 = freeze, 0 = unfreeze
                self._apply_freeze(bool(int(msg.wParam)))
                continue
            if msg.message == WM_HOTKEY and not self._frozen:
                cb = handlers.get(int(msg.wParam))
                if cb is not None:
                    try:
                        self.app.after(0, cb)
                    except tk.TclError:
                        break

        self._unregister_all()

    def _register_all(self) -> None:
        self._status = []
        for hid, vk, name in self._specs:
            ok = user32.RegisterHotKey(None, hid, MOD_NOREPEAT, vk)
            if not ok:
                ok = user32.RegisterHotKey(None, hid, 0, vk)
            self._status.append(f"{'OK' if ok else 'FAIL'}  {name}")
        self._frozen = False

    def _unregister_all(self) -> None:
        for hid, _vk, _name in self._specs:
            user32.UnregisterHotKey(None, hid)

    def _apply_freeze(self, frozen: bool) -> None:
        if frozen and not self._frozen:
            self._unregister_all()
            self._frozen = True
            self._status = ["FROZEN  (hotkeys off for typing)"]
        elif not frozen and self._frozen:
            self._register_all()
        try:
            HOTKEY_LOG.write_text(
                "Hotkey registration:\n" + "\n".join(self._status) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def set_frozen(self, frozen: bool) -> None:
        """Called from the UI thread: ask hotkey thread to freeze/unfreeze."""
        if self.thread_id:
            user32.PostThreadMessageW(
                self.thread_id, WM_DT_SET_FREEZE, 1 if frozen else 0, 0
            )

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# --- overlay ---

class DeathTracker(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.data = load_data()
        self.settings_win: SettingsWindow | None = None
        self._drag_offset = (0, 0)
        self._press_root = (0, 0)
        self._did_drag = False
        self._hotkeys = HotkeyThread(self)

        self.title("Death Tracker")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANS, bd=0, highlightthickness=0)

        try:
            self.wm_attributes("-transparentcolor", TRANS)
        except tk.TclError:
            pass

        # keep PhotoImage refs alive or Tk GC blanks them
        self._counter_photo: ImageTk.PhotoImage | None = None
        self._flash_photos: list[ImageTk.PhotoImage] = []
        self._float_photos: list[ImageTk.PhotoImage] = []
        self._flash_job: str | None = None
        self._float_job: str | None = None
        self._counter_min_size: tuple[int, int] = (1, 1)
        self._counter_content_size: tuple[int, int] = (1, 1)
        self._polished = False
        # room for the +1 popup so it doesn't clip
        self._float_reserve = 160   # right of counter
        self._float_headroom = 90   # above counter

        self.label = tk.Label(
            self,
            bg=TRANS,
            bd=0,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            padx=0,
            pady=0,
        )
        # place() so we can keep a reserved transparent zone for the float anim
        self.label.place(x=0, y=0)

        self.float_label = tk.Label(
            self,
            bg=TRANS,
            bd=0,
            highlightthickness=0,
            borderwidth=0,
            padx=0,
            pady=0,
        )
        self.float_label.place_forget()

        self._redraw_counter(repolish=True)

        for w in (self, self.label, self.float_label):
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Double-Button-1>", self._on_double_click)
            w.bind("<Button-3>", lambda e: self.open_settings())

        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._place_initial()

        # polish once after HWND exists (doing it every death caused a box flash)
        self.after(1, self._make_transparent)
        self.after(80, self._make_transparent)

        self._hotkeys.start()
        self.after(400, self._warn_if_hotkeys_failed)

        if not self.data.get("locked"):
            self.after(300, self.open_settings)

    def _make_transparent(self) -> None:
        try:
            polish_overlay_window(self, TRANS)
            self.label.configure(bg=TRANS)
            self._polished = True
        except Exception:
            pass

    # ── display ──────────────────────────────────────────────────

    def counter_color(self) -> str:
        return normalize_hex(self.data.get("counter_color"), DEFAULT_COUNTER_COLOR)

    def suck_color(self) -> str:
        return normalize_hex(self.data.get("suck_color"), DEFAULT_SUCK_COLOR)

    def suck_text(self) -> str:
        """Words after '+1' in the death popup (may be empty)."""
        return str(self.data.get("suck_text") or "").strip()[:40]

    def popup_phrase(self) -> str:
        extra = self.suck_text()
        return f"+1  {extra}" if extra else "+1"

    def set_suck_text(self, text: str) -> None:
        # Empty is allowed: popup shows only "+1"
        self.data["suck_text"] = str(text or "").strip()[:40]
        save_data(self.data)
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.refresh_color_swatches()
            except tk.TclError:
                pass

    def set_counter_color(self, color: str) -> None:
        self.data["counter_color"] = normalize_hex(color, DEFAULT_COUNTER_COLOR)
        save_data(self.data)
        self.refresh(repolish=False)
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.refresh_color_swatches()
            except tk.TclError:
                pass

    def set_suck_color(self, color: str) -> None:
        self.data["suck_color"] = normalize_hex(color, DEFAULT_SUCK_COLOR)
        save_data(self.data)
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.refresh_color_swatches()
            except tk.TclError:
                pass

    def _phrase(self) -> str:
        n = int(self.data["session_deaths"])
        unit = "time" if n == 1 else "times"
        return f"I died {n} {unit}"

    def _font_size(self) -> int:
        return int(self.data.get("font_size") or 28)

    def _stable_min_size(self, phrase: str, font_size: int) -> tuple[int, int]:
        """Pad bitmap so width never shrinks (digit growth won't resize the HWND)."""
        # Reserve room for an extra digit so 9→10 / 99→100 doesn't flicker
        n = int(self.data["session_deaths"])
        probes = [phrase, f"I died {n} times", "I died 8 times", "I died 88 times"]
        if n >= 10:
            probes.append("I died 888 times")
        w = self._counter_min_size[0]
        h = self._counter_min_size[1]
        for p in probes:
            pw, ph = measure_text_size(p, font_size)
            w, h = max(w, pw), max(h, ph)
        self._counter_min_size = (w, h)
        return w, h

    def _make_counter_photo(
        self, phrase: str, fill: str
    ) -> tuple[ImageTk.PhotoImage, tuple[int, int]]:
        size = self._font_size()
        min_size = self._stable_min_size(phrase, size)
        return render_keyed_text(phrase, size, fill, TRANS, min_size=min_size)

    def _layout_shell(self, content_w: int, content_h: int) -> None:
        """
        Size the overlay:
          [ transparent headroom for float rise ]
          [ counter text | transparent right strip for float ]
        All one window so no second-HWND black box.
        """
        self._counter_content_size = (content_w, content_h)
        total_w = content_w + self._float_reserve
        total_h = self._float_headroom + content_h
        try:
            x, y = self.winfo_x(), self.winfo_y()
            # Grow upward when headroom is added so the counter stays put on screen
            prev_h = getattr(self, "_last_shell_h", 0) or 0
            if prev_h and total_h > prev_h:
                y = y - (total_h - prev_h)
            self._last_shell_h = total_h
            self.geometry(f"{total_w}x{total_h}+{x}+{y}")
        except tk.TclError:
            self._last_shell_h = total_h
            self.geometry(f"{total_w}x{total_h}")
        # Counter sits under the headroom so the float can rise into empty space
        self.label.place(x=0, y=self._float_headroom)
        self.configure(width=total_w, height=total_h, bg=TRANS)

    def _redraw_counter(
        self, color: str | None = None, *, repolish: bool = False
    ) -> None:
        """Swap the counter image. Avoid re-polishing every time (causes a box flash)."""
        fill = color or self.counter_color()
        photo, dims = self._make_counter_photo(self._phrase(), fill)
        self._counter_photo = photo  # prevent GC
        # Swap image only — keep bg keyed, do not touch layered styles
        self.label.configure(image=photo, bg=TRANS)
        self._layout_shell(dims[0], dims[1])
        if repolish or not self._polished:
            self.update_idletasks()
            self._make_transparent()

    def refresh(self, *, repolish: bool = False) -> None:
        self._redraw_counter(repolish=repolish)
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.sync_from_data()
            except tk.TclError:
                pass

    def apply_font_size(self, size: int) -> None:
        self.data["font_size"] = max(14, min(72, int(size)))
        # Font change can alter layout — allow one polish after
        self._counter_min_size = (1, 1)
        self.refresh(repolish=True)

    def _cancel_flash(self) -> None:
        if self._flash_job is not None:
            try:
                self.after_cancel(self._flash_job)
            except tk.TclError:
                pass
            self._flash_job = None

    def _smooth_flash(self) -> None:
        """
        Smooth white flash on the new count, then ease back to counter color.
        Pre-renders frames and only swaps images (no transparency re-apply).
        """
        self._cancel_flash()
        phrase = self._phrase()
        base = self.counter_color()
        size = self._font_size()
        min_size = self._stable_min_size(phrase, size)

        # Peak bright, then ease back — ~280ms total
        # t=0 white, t=1 base color (ease-out so it hangs bright briefly)
        steps = 10
        frame_ms = 28
        photos: list[ImageTk.PhotoImage] = []
        for i in range(steps):
            t = i / (steps - 1)
            # ease-in cubic: stay closer to white early, settle into base
            ease = t * t * t
            col = blend_hex(TEXT_FLASH, base, ease)
            photo, _ = render_keyed_text(
                phrase, size, col, TRANS, min_size=min_size
            )
            photos.append(photo)

        self._flash_photos = photos  # keep refs for the whole anim

        def show(i: int) -> None:
            if i >= len(photos):
                self._flash_job = None
                # Final settle on exact base color
                self._redraw_counter(base, repolish=False)
                return
            self._counter_photo = photos[i]
            try:
                self.label.configure(image=photos[i], bg=TRANS)
            except tk.TclError:
                self._flash_job = None
                return
            self._flash_job = self.after(frame_ms, lambda: show(i + 1))

        show(0)

    # ── actions ──────────────────────────────────────────────────

    def die(self) -> None:
        self.data["session_deaths"] += 1
        self.data["total_deaths"] += 1
        save_data(self.data)
        # Update settings labels only (don't double-redraw before flash)
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.sync_from_data()
            except tk.TclError:
                pass
        self._smooth_flash()
        self._spawn_suck_float()

    def undo(self) -> None:
        if self.data["session_deaths"] > 0:
            self.data["session_deaths"] -= 1
        if self.data["total_deaths"] > 0:
            self.data["total_deaths"] -= 1
        save_data(self.data)
        self.refresh()

    def reset_session(self) -> None:
        self.data["session_deaths"] = 0
        save_data(self.data)
        self.refresh()

    def unlock_for_move(self) -> None:
        self.data["locked"] = False
        save_data(self.data)
        self.label.configure(cursor="fleur")
        self.open_settings()
        if self.settings_win is not None:
            try:
                self.settings_win.move_mode.set(True)
                self.settings_win.sync_from_data()
            except tk.TclError:
                pass

    # ── float anim (same window — no popup = no black box) ───────

    def _cancel_float(self) -> None:
        if self._float_job is not None:
            try:
                self.after_cancel(self._float_job)
            except tk.TclError:
                pass
            self._float_job = None
        try:
            self.float_label.place_forget()
            self.float_label.configure(image="")
        except tk.TclError:
            pass

    def _spawn_suck_float(self) -> None:
        """
        Animate the +1 popup inside the overlay: long rise through the
        transparent headroom, fading out instead of clipping off-screen.
        """
        self._cancel_float()
        self.update_idletasks()

        size = max(12, int(self.data.get("font_size", 28) * 0.55))
        suck = self.suck_color()
        phrase = self.popup_phrase()
        pw, ph = measure_text_size(phrase, size)

        # Room on the right + enough headroom to rise fully without clipping
        need_w = pw + 20
        need_h = ph + 16
        if need_w > self._float_reserve:
            self._float_reserve = need_w
        # Rise almost the full headroom (leave a few px so it never hits the rim)
        rise_px = 78
        if self._float_headroom < rise_px + 12:
            self._float_headroom = rise_px + 12
        cw, ch = self._counter_content_size
        self._layout_shell(cw, ch)

        # Pre-render fade frames: full opacity → gone (smooth dissolve)
        steps = 36
        duration_ms = 1400
        photos: list[ImageTk.PhotoImage] = []
        for i in range(steps):
            t = i / max(1, steps - 1)
            # Stay solid briefly, then ease opacity to 0
            if t < 0.15:
                opacity = 1.0
            else:
                u = (t - 0.15) / 0.85
                # smoothstep fade
                opacity = 1.0 - (u * u * (3 - 2 * u))
            # Slightly dim the color as it fades so it softens out
            col = blend_hex(suck, shade_hex(suck, 0.35), 1.0 - opacity)
            p, _ = render_keyed_text(
                phrase,
                size,
                col,
                TRANS,
                min_size=(pw, ph),
                opacity=max(0.0, opacity),
            )
            photos.append(p)
        self._float_photos = photos

        # Start aligned with the counter, in the right gutter
        base_x = cw + 6
        base_y = self._float_headroom + max(0, (ch - ph) // 2)

        self.float_label.configure(image=photos[0], bg=TRANS)
        self.float_label.place(x=base_x, y=base_y)

        def ease_out(t: float) -> float:
            return 1.0 - (1.0 - t) ** 3

        def frame(i: int) -> None:
            if i >= steps:
                try:
                    self.float_label.place_forget()
                    self.float_label.configure(image="")
                except tk.TclError:
                    pass
                self._float_job = None
                return
            t = i / max(1, steps - 1)
            e = ease_out(t)
            x = int(base_x + 10 * e)
            y = int(base_y - rise_px * e)
            try:
                self.float_label.configure(image=photos[i], bg=TRANS)
                self.float_label.place(x=x, y=y)
            except tk.TclError:
                self._float_job = None
                return
            self._float_job = self.after(duration_ms // steps, lambda: frame(i + 1))

        frame(0)

    # ── position / click / drag ──────────────────────────────────

    def _place_initial(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = self.data.get("pos_x"), self.data.get("pos_y")
        if x is None or y is None:
            x, y = sw - w - 48, 48
        x = int(max(0, min(sw - max(w, 40), x)))
        y = int(max(0, min(sh - max(h, 20), y)))
        self.geometry(f"+{x}+{y}")

    def is_movable(self) -> bool:
        if not self.data.get("locked"):
            return True
        if self.settings_win is not None:
            try:
                if (
                    self.settings_win.winfo_exists()
                    and self.settings_win.move_mode.get()
                ):
                    return True
            except tk.TclError:
                pass
        return False

    def _on_press(self, event: tk.Event) -> None:
        self._did_drag = False
        self._press_root = (event.x_root, event.y_root)
        # Ignore drag starts when locked (unless settings move mode is on)
        if not self.is_movable():
            self._drag_offset = (0, 0)
            return
        self._drag_offset = (
            event.x_root - self.winfo_x(),
            event.y_root - self.winfo_y(),
        )

    def _on_drag(self, event: tk.Event) -> None:
        if not self.is_movable():
            return
        dx = abs(event.x_root - self._press_root[0])
        dy = abs(event.y_root - self._press_root[1])
        # Bigger slop so tiny mouse shakes while clicking don't count as a drag
        if dx < 8 and dy < 8:
            return
        self._did_drag = True
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.geometry(f"+{x}+{y}")
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.update_pos_labels(x, y)
            except tk.TclError:
                pass

    def _on_release(self, event: tk.Event) -> None:
        if self._did_drag and self.is_movable():
            self.data["pos_x"] = self.winfo_x()
            self.data["pos_y"] = self.winfo_y()
            # Re-lock after you finish dragging so the next game click won't move it
            self.data["locked"] = True
            self.label.configure(cursor="hand2")
            save_data(self.data)
            if self.settings_win is not None:
                try:
                    if self.settings_win.winfo_exists():
                        self.settings_win.move_mode.set(False)
                        self.settings_win.sync_from_data()
                except tk.TclError:
                    pass
            return
        # Single click does nothing (settings is double-click)

    def _on_double_click(self, _event: tk.Event | None = None) -> None:
        self.open_settings()

    def save_current_position(self, lock: bool = True) -> None:
        self.update_idletasks()
        self.data["pos_x"] = self.winfo_x()
        self.data["pos_y"] = self.winfo_y()
        if lock:
            self.data["locked"] = True
            self.label.configure(cursor="hand2")
        save_data(self.data)
        self._make_transparent()

    def nudge(self, dx: int, dy: int) -> None:
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")
        self.data["pos_x"] = x
        self.data["pos_y"] = y
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.update_pos_labels(x, y)
            except tk.TclError:
                pass

    # ── settings ─────────────────────────────────────────────────

    def open_settings(self) -> None:
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.deiconify()
                    self.settings_win.lift()
                    self.settings_win.attributes("-topmost", True)
                    self.settings_win.focus_force()
                    return
            except tk.TclError:
                pass
        self.settings_win = SettingsWindow(self)

    def _warn_if_hotkeys_failed(self) -> None:
        self._hotkeys._ready.wait(timeout=2.0)
        if self.data.get("hotkeys_frozen"):
            return
        fails = [s for s in self._hotkeys._status if s.startswith("FAIL")]
        if fails:
            messagebox.showwarning(
                "Hotkeys",
                "Some hotkeys failed to register:\n\n"
                + "\n".join(fails)
                + "\n\nSee hotkey_status.txt",
                parent=self,
            )

    def set_hotkeys_frozen(self, frozen: bool) -> None:
        """Unregister hotkeys so D/R/H/U/End go to other apps (chat, browser, etc.)."""
        self.data["hotkeys_frozen"] = bool(frozen)
        save_data(self.data)
        self._hotkeys.set_frozen(bool(frozen))
        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.sync_hotkey_freeze_ui()
            except tk.TclError:
                pass

    def shutdown(self) -> None:
        try:
            self.data["pos_x"] = self.winfo_x()
            self.data["pos_y"] = self.winfo_y()
        except tk.TclError:
            pass
        save_data(self.data)
        self._hotkeys.stop()
        if self.settings_win is not None:
            try:
                self.settings_win.destroy()
            except tk.TclError:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass


# --- settings ---

class SettingsWindow(tk.Toplevel):
    def __init__(self, app: DeathTracker) -> None:
        super().__init__(app)
        self.app = app
        self.move_mode = tk.BooleanVar(value=not app.data.get("locked", False))
        self._preset_target = tk.StringVar(value="counter")  # or "suck"

        self.title("Electro's death tracker")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Style notebook to match dark theme a bit
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=PANEL,
            foreground=FG,
            padding=[12, 6],
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BTN_ACTIVE)],
            foreground=[("selected", GOLD)],
        )
        style.configure("TFrame", background=BG)

        tk.Label(
            self,
            text="Electro's death tracker",
            font=("Segoe UI", 14, "bold"),
            fg=GOLD,
            bg=BG,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        tk.Label(
            self,
            text="Double-click the overlay to open this menu.  ·  End = quit",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        tab_general = tk.Frame(notebook, bg=BG)
        tab_position = tk.Frame(notebook, bg=BG)
        tab_colors = tk.Frame(notebook, bg=BG)
        notebook.add(tab_general, text="  General  ")
        notebook.add(tab_position, text="  Position  ")
        notebook.add(tab_colors, text="  Colors  ")

        self._build_general(tab_general)
        self._build_position(tab_position)
        self._build_colors(tab_colors)

        # Always-visible footer (every tab)
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(4, 16))
        self._btn(foot, "Quit tracker", self._quit_app, width=14).pack(side="left")

        freeze_wrap = tk.Frame(foot, bg=BG)
        freeze_wrap.pack(side="right")
        self.hotkey_freeze = tk.BooleanVar(
            value=bool(app.data.get("hotkeys_frozen", False))
        )
        self.freeze_chk = tk.Checkbutton(
            freeze_wrap,
            text="Freeze hotkeys (type freely)",
            variable=self.hotkey_freeze,
            command=self._on_hotkey_freeze,
            font=("Segoe UI", 9, "bold"),
            fg=GOLD,
            bg=BG,
            selectcolor=PANEL,
            activebackground=BG,
            activeforeground=GOLD,
            anchor="e",
            cursor="hand2",
        )
        self.freeze_chk.pack(side="right")
        self._btn(foot, "Done", self._on_close, width=10).pack(side="right", padx=(0, 12))

        self.sync_from_data()
        self.update_pos_labels(app.winfo_x(), app.winfo_y())
        self.refresh_color_swatches()
        self.sync_hotkey_freeze_ui()

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        ww, wh = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 3}")

    def _build_general(self, parent: tk.Frame) -> None:
        app = self.app
        stats = tk.Frame(parent, bg=PANEL, padx=12, pady=10)
        stats.pack(fill="x", padx=8, pady=8)

        self.session_lbl = tk.Label(
            stats, text="", font=("Segoe UI", 11, "bold"), fg=FG, bg=PANEL
        )
        self.session_lbl.pack(anchor="w")
        self.total_lbl = tk.Label(
            stats, text="", font=("Segoe UI", 9), fg=MUTED, bg=PANEL
        )
        self.total_lbl.pack(anchor="w", pady=(2, 0))

        btn_row = tk.Frame(stats, bg=PANEL)
        btn_row.pack(fill="x", pady=(10, 0))
        self._btn(btn_row, "Die (test)", app.die).pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Undo", app.undo).pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Reset session", app.reset_session).pack(side="left")

        size_fr = tk.LabelFrame(
            parent, text="  Text size  ", font=("Segoe UI", 9, "bold"), fg=GOLD, bg=BG
        )
        size_fr.pack(fill="x", padx=8, pady=8)
        self.size_var = tk.IntVar(value=int(app.data.get("font_size") or 28))
        row = tk.Frame(size_fr, bg=BG)
        row.pack(fill="x", padx=12, pady=10)
        tk.Label(row, text="Small", font=("Segoe UI", 8), fg=MUTED, bg=BG).pack(
            side="left"
        )
        tk.Scale(
            row,
            from_=16,
            to=48,
            orient="horizontal",
            variable=self.size_var,
            command=self._on_size,
            bg=BG,
            fg=FG,
            highlightthickness=0,
            troughcolor=PANEL,
            activebackground=ACCENT,
            length=220,
            showvalue=True,
        ).pack(side="left", padx=8)
        tk.Label(row, text="Large", font=("Segoe UI", 8), fg=MUTED, bg=BG).pack(
            side="left"
        )

        keys = tk.LabelFrame(
            parent,
            text="  Hotkeys (work in-game)  ",
            font=("Segoe UI", 9, "bold"),
            fg=GOLD,
            bg=BG,
        )
        keys.pack(fill="x", padx=8, pady=8)
        help_txt = (
            "  D     +1 death  (custom +1 popup)\n"
            "  R     reset session to 0\n"
            "  H     open settings\n"
            "  U     unlock so you can drag it\n"
            "  End   quit completely\n"
            "\n"
            "  Double-click the overlay to open settings\n"
            "  Overlay stays locked while you play (U to move)"
        )
        tk.Label(
            keys,
            text=help_txt,
            font=("Consolas", 9),
            fg=FG,
            bg=BG,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=12, pady=10)

    def _build_position(self, parent: tk.Frame) -> None:
        app = self.app
        pos = tk.Frame(parent, bg=BG)
        pos.pack(fill="both", expand=True, padx=8, pady=8)

        self.pos_lbl = tk.Label(pos, text="", font=("Consolas", 10), fg=FG, bg=BG)
        self.pos_lbl.pack(anchor="w", pady=(4, 8))

        tk.Checkbutton(
            pos,
            text="Move mode: drag the overlay (or use arrows)",
            variable=self.move_mode,
            command=self._on_move_mode,
            font=("Segoe UI", 9),
            fg=FG,
            bg=BG,
            selectcolor=PANEL,
            activebackground=BG,
            activeforeground=FG,
            anchor="w",
        ).pack(fill="x", pady=4)

        arrows = tk.Frame(pos, bg=BG)
        arrows.pack(pady=10)

        def arrow(txt: str, dx: int, dy: int) -> tk.Button:
            return self._btn(arrows, txt, lambda: app.nudge(dx, dy), width=4)

        arrow("↑", 0, -12).grid(row=0, column=1, padx=3, pady=3)
        arrow("←", -12, 0).grid(row=1, column=0, padx=3, pady=3)
        tk.Label(arrows, text="12px", font=("Segoe UI", 8), fg=MUTED, bg=BG).grid(
            row=1, column=1, padx=3, pady=3
        )
        arrow("→", 12, 0).grid(row=1, column=2, padx=3, pady=3)
        arrow("↓", 0, 12).grid(row=2, column=1, padx=3, pady=3)

        fine = tk.Frame(pos, bg=BG)
        fine.pack(pady=(0, 10))
        for label, dx, dy in (
            ("1px ←", -1, 0),
            ("1px →", 1, 0),
            ("1px ↑", 0, -1),
            ("1px ↓", 0, 1),
        ):
            self._btn(fine, label, lambda x=dx, y=dy: app.nudge(x, y), width=6).pack(
                side="left", padx=2
            )

        self._btn(
            pos, "Save position & lock overlay", self._save_and_lock, width=28
        ).pack(pady=(8, 4))

        tk.Label(
            pos,
            text="Tip: unlock with U if the overlay is locked.",
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=BG,
        ).pack(pady=(8, 0))

    def _build_colors(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text="Pick colors that stand out on your game’s background.",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
        ).pack(anchor="w", padx=12, pady=(12, 8))

        # Death counter color
        row1 = tk.Frame(parent, bg=PANEL, padx=12, pady=12)
        row1.pack(fill="x", padx=8, pady=6)
        tk.Label(
            row1,
            text="Death counter  (“I died X times”)",
            font=("Segoe UI", 10, "bold"),
            fg=FG,
            bg=PANEL,
        ).pack(anchor="w")

        sw1 = tk.Frame(row1, bg=PANEL)
        sw1.pack(fill="x", pady=(8, 0))
        self.counter_swatch = tk.Label(
            sw1,
            text="   ",
            width=4,
            bg=self.app.counter_color(),
            relief="solid",
            bd=1,
        )
        self.counter_swatch.pack(side="left")
        self.counter_hex_lbl = tk.Label(
            sw1,
            text=self.app.counter_color(),
            font=("Consolas", 10),
            fg=MUTED,
            bg=PANEL,
        )
        self.counter_hex_lbl.pack(side="left", padx=10)
        self._btn(sw1, "Pick color…", self._pick_counter, width=12).pack(side="left")
        self._btn(
            sw1,
            "Reset",
            lambda: self.app.set_counter_color(DEFAULT_COUNTER_COLOR),
            width=7,
        ).pack(side="left", padx=(6, 0))

        self.counter_preview = tk.Label(
            row1,
            text="I died 3 times",
            font=("Segoe UI", 16, "bold"),
            fg=self.app.counter_color(),
            bg="#2a2030",
            padx=10,
            pady=6,
        )
        self.counter_preview.pack(anchor="w", pady=(10, 0))

        # +1 popup text + color
        row2 = tk.Frame(parent, bg=PANEL, padx=12, pady=12)
        row2.pack(fill="x", padx=8, pady=6)
        tk.Label(
            row2,
            text="+1 popup  (shows when you die)",
            font=("Segoe UI", 10, "bold"),
            fg=FG,
            bg=PANEL,
        ).pack(anchor="w")

        text_row = tk.Frame(row2, bg=PANEL)
        text_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            text_row, text="+1", font=("Segoe UI", 10, "bold"), fg=FG, bg=PANEL
        ).pack(side="left")
        self.suck_text_var = tk.StringVar(value=self.app.suck_text())
        self.suck_text_entry = tk.Entry(
            text_row,
            textvariable=self.suck_text_var,
            font=("Segoe UI", 10),
            fg=FG,
            bg=BTN_BG,
            insertbackground=FG,
            relief="flat",
            width=22,
        )
        self.suck_text_entry.pack(side="left", padx=(8, 6), ipady=4)
        self.suck_text_entry.bind("<Return>", lambda _e: self._apply_suck_text())
        self.suck_text_entry.bind("<FocusOut>", lambda _e: self._apply_suck_text())
        self._btn(text_row, "Apply", self._apply_suck_text, width=7).pack(side="left")
        self._btn(
            text_row,
            "Reset",
            lambda: (self.suck_text_var.set("I SUCK"), self._apply_suck_text()),
            width=7,
        ).pack(side="left", padx=(6, 0))

        tk.Label(
            row2,
            text="Anything after +1 (max 40). Leave blank for just +1.",
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=PANEL,
        ).pack(anchor="w", pady=(6, 0))

        sw2 = tk.Frame(row2, bg=PANEL)
        sw2.pack(fill="x", pady=(10, 0))
        self.suck_swatch = tk.Label(
            sw2, text="   ", width=4, bg=self.app.suck_color(), relief="solid", bd=1
        )
        self.suck_swatch.pack(side="left")
        self.suck_hex_lbl = tk.Label(
            sw2,
            text=self.app.suck_color(),
            font=("Consolas", 10),
            fg=MUTED,
            bg=PANEL,
        )
        self.suck_hex_lbl.pack(side="left", padx=10)
        self._btn(sw2, "Pick color…", self._pick_suck, width=12).pack(side="left")
        self._btn(
            sw2,
            "Reset",
            lambda: self.app.set_suck_color(DEFAULT_SUCK_COLOR),
            width=7,
        ).pack(side="left", padx=(6, 0))

        self.suck_preview = tk.Label(
            row2,
            text=self.app.popup_phrase(),
            font=("Segoe UI", 14, "bold"),
            fg=self.app.suck_color(),
            bg="#2a2030",
            padx=10,
            pady=6,
        )
        self.suck_preview.pack(anchor="w", pady=(10, 0))

        # Presets
        presets = tk.LabelFrame(
            parent,
            text="  Quick presets  ",
            font=("Segoe UI", 9, "bold"),
            fg=GOLD,
            bg=BG,
        )
        presets.pack(fill="x", padx=8, pady=8)

        target_row = tk.Frame(presets, bg=BG)
        target_row.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(
            target_row, text="Apply preset to:", font=("Segoe UI", 9), fg=MUTED, bg=BG
        ).pack(side="left")
        tk.Radiobutton(
            target_row,
            text="Death counter",
            variable=self._preset_target,
            value="counter",
            font=("Segoe UI", 9),
            fg=FG,
            bg=BG,
            selectcolor=PANEL,
            activebackground=BG,
            activeforeground=FG,
        ).pack(side="left", padx=(8, 4))
        tk.Radiobutton(
            target_row,
            text="+1 popup",
            variable=self._preset_target,
            value="suck",
            font=("Segoe UI", 9),
            fg=FG,
            bg=BG,
            selectcolor=PANEL,
            activebackground=BG,
            activeforeground=FG,
        ).pack(side="left", padx=4)

        grid = tk.Frame(presets, bg=BG)
        grid.pack(padx=10, pady=(4, 12))
        for i, (name, hex_c) in enumerate(COLOR_PRESETS):
            b = tk.Button(
                grid,
                text=name,
                command=lambda c=hex_c: self._apply_preset(c),
                font=("Segoe UI", 8),
                fg="#111",
                bg=hex_c,
                activebackground=hex_c,
                relief="flat",
                cursor="hand2",
                width=8,
                padx=4,
                pady=4,
            )
            b.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="nsew")

        tk.Label(
            parent,
            text="Colors save automatically and stick between games.",
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=BG,
        ).pack(anchor="w", padx=12, pady=(0, 10))

        self._btn(
            parent, "Test death animation", self.app.die, width=20
        ).pack(anchor="w", padx=12, pady=(0, 12))

    # ── helpers ──────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, width: int | None = None) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=("Segoe UI", 9),
            fg=FG,
            bg=BTN_BG,
            activebackground=BTN_ACTIVE,
            activeforeground=FG,
            relief="flat",
            cursor="hand2",
            width=width if width is not None else 0,
            padx=8,
            pady=4,
        )

    def refresh_color_swatches(self) -> None:
        cc = self.app.counter_color()
        sc = self.app.suck_color()
        try:
            self.counter_swatch.configure(bg=cc)
            self.counter_hex_lbl.configure(text=cc)
            self.counter_preview.configure(fg=cc)
            self.suck_swatch.configure(bg=sc)
            self.suck_hex_lbl.configure(text=sc)
            self.suck_preview.configure(fg=sc, text=self.app.popup_phrase())
            if hasattr(self, "suck_text_var"):
                # Don't clobber typing unless values differ
                if self.suck_text_var.get().strip() != self.app.suck_text():
                    self.suck_text_var.set(self.app.suck_text())
        except tk.TclError:
            pass

    def _apply_suck_text(self) -> None:
        self.app.set_suck_text(self.suck_text_var.get())
        try:
            self.suck_text_var.set(self.app.suck_text())
            self.suck_preview.configure(text=self.app.popup_phrase())
        except tk.TclError:
            pass

    def _on_hotkey_freeze(self) -> None:
        self.app.set_hotkeys_frozen(bool(self.hotkey_freeze.get()))

    def sync_hotkey_freeze_ui(self) -> None:
        frozen = bool(self.app.data.get("hotkeys_frozen", False))
        try:
            self.hotkey_freeze.set(frozen)
            if frozen:
                self.freeze_chk.configure(text="Hotkeys FROZEN (type freely)")
            else:
                self.freeze_chk.configure(text="Freeze hotkeys (type freely)")
        except tk.TclError:
            pass

    def _pick_counter(self) -> None:
        _rgb, hex_c = colorchooser.askcolor(
            color=self.app.counter_color(),
            title="Death counter color",
            parent=self,
        )
        if hex_c:
            self.app.set_counter_color(hex_c)
            self.refresh_color_swatches()

    def _pick_suck(self) -> None:
        _rgb, hex_c = colorchooser.askcolor(
            color=self.app.suck_color(),
            title="+1 popup color",
            parent=self,
        )
        if hex_c:
            self.app.set_suck_color(hex_c)
            self.refresh_color_swatches()

    def _apply_preset(self, hex_c: str) -> None:
        if self._preset_target.get() == "suck":
            self.app.set_suck_color(hex_c)
        else:
            self.app.set_counter_color(hex_c)
        self.refresh_color_swatches()

    def sync_from_data(self) -> None:
        d = self.app.data
        n = d["session_deaths"]
        unit = "time" if n == 1 else "times"
        self.session_lbl.configure(text=f"I died {n} {unit}  (this session)")
        self.total_lbl.configure(text=f"All-time total: {d['total_deaths']}")
        state = "move mode ON" if self.move_mode.get() else (
            "locked" if d.get("locked") else "unlocked"
        )
        self.pos_lbl.configure(
            text=f"X={d.get('pos_x')}  Y={d.get('pos_y')}   [{state}]"
        )
        self.refresh_color_swatches()

    def update_pos_labels(self, x: int, y: int) -> None:
        state = "move mode ON" if self.move_mode.get() else (
            "locked" if self.app.data.get("locked") else "unlocked"
        )
        self.pos_lbl.configure(text=f"X={x}  Y={y}   [{state}]")

    def _on_move_mode(self) -> None:
        if self.move_mode.get():
            self.app.label.configure(cursor="fleur")
        else:
            self.app.label.configure(cursor="hand2")
        self.sync_from_data()

    def _on_size(self, _val=None) -> None:
        self.app.apply_font_size(self.size_var.get())
        save_data(self.app.data)

    def _save_and_lock(self) -> None:
        self.app.save_current_position(lock=True)
        self.move_mode.set(False)
        self.app.label.configure(cursor="hand2")
        self.sync_from_data()
        messagebox.showinfo(
            "Saved",
            "Position locked.\n\n"
            "Click the overlay to open settings again.\n"
            "U = unlock & move\n"
            "End = quit",
            parent=self,
        )

    def _quit_app(self) -> None:
        self.app.shutdown()

    def _on_close(self) -> None:
        # Always re-lock when settings closes so you can't drag mid-game by accident
        self.app.save_current_position(lock=True)
        self.move_mode.set(False)
        try:
            self.app.label.configure(cursor="hand2")
        except tk.TclError:
            pass
        self.destroy()
        self.app.settings_win = None
        self.app._make_transparent()


# --- entry ---

def _single_instance() -> ctypes.c_void_p | None:
    handle = kernel32.CreateMutexW(None, False, "Local\\DeathTrackerOverlayMutex_v2")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def main() -> None:
    mutex = _single_instance()
    if mutex is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Death Tracker",
            "Death Tracker is already running.\n\n"
            "Press  End  to quit it,\n"
            "or end pythonw.exe in Task Manager.",
        )
        root.destroy()
        return

    app = DeathTracker()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
