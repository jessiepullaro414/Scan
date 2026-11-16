"""The app window's title bar in the app's own colours (Windows).

pywebview's window on Windows is a WinForms form with the system's title bar: white unless Windows
itself is set to dark, so above the app's dark page it looked out of place. :func:`paint_title_bar`
sets the bar's colours through DWM (``DwmSetWindowAttribute``):

* dark mode for the caption buttons (``DWMWA_USE_IMMERSIVE_DARK_MODE``, Windows 10 1809 and later);
* the caption, its text and the window border in the app's panel, text and line colours
  (``DWMWA_CAPTION_COLOR`` / ``TEXT_COLOR`` / ``BORDER_COLOR``, Windows 11; Windows 10 keeps its
  own colours and only takes the dark mode).

The page tells the window when the user switches between the dark and the light theme
(``window.pywebview.api.set_theme``, :class:`WindowApi`), so the bar follows it. Elsewhere than
Windows, and where DWM refuses, nothing happens.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Any

__all__ = ["THEMES", "colorref", "find_windows", "paint_title_bar", "WindowApi"]

log = logging.getLogger(__name__)

# the app's colours (ui/src/index.css): caption = --c-panel, text = --c-fg, border = --c-surface-3
THEMES: dict[str, dict[str, str]] = {
    "dark": {"caption": "#111419", "text": "#e7eaee", "border": "#242931"},
    "light": {"caption": "#ffffff", "text": "#12161c", "border": "#e8eaee"},
}
_DARK_MODE, _DARK_MODE_OLD = 20, 19  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before Windows 10 20H1)
_BORDER, _CAPTION, _TEXT = 34, 35, 36  # DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR


def colorref(hex_color: str) -> int:
    """``#rrggbb`` as a Win32 COLORREF (0x00BBGGRR)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #rrggbb, got {hex_color!r}")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def find_windows(title: str, pid: int | None = None) -> list[int]:
    """Handles of this process's (or ``pid``'s) top-level windows titled exactly ``title``."""
    if not sys.platform.startswith("win"):
        return []
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = os.getpid() if pid is None else int(pid)
    found: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(proc(each), 0)
    return found


def paint_title_bar(hwnd: int, theme: str) -> bool:
    """Colour the title bar of ``hwnd`` for ``theme`` ("dark" or "light"). False where nothing took."""
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {sorted(THEMES)}, got {theme!r}")
    if not sys.platform.startswith("win") or not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return False

    def put(attr: int, value: int) -> bool:
        v = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v)) == 0

    dark = int(theme == "dark")
    ok = put(_DARK_MODE, dark) or put(_DARK_MODE_OLD, dark)
    c = THEMES[theme]
    for attr, key in ((_CAPTION, "caption"), (_TEXT, "text"), (_BORDER, "border")):
        ok = put(attr, colorref(c[key])) or ok  # Windows 10 refuses these: it keeps the dark mode only
    return ok


class WindowApi:
    """What the page may call on the window (pywebview ``js_api``): ``set_theme``. Only methods are
    public; pywebview hands the page every public attribute."""

    def __init__(self, title: str) -> None:
        self._title = title
        self._theme = "dark"

    def set_theme(self, theme: str) -> bool:
        """The page's theme changed (or the page has just loaded): colour the title bar to match."""
        if theme not in THEMES:
            return False
        self._theme = theme
        return self.paint()

    def paint(self) -> bool:
        """Colour every window of this process with the title for the current theme."""
        done = False
        for hwnd in find_windows(self._title):
            try:
                done = paint_title_bar(hwnd, self._theme) or done
            except Exception as e:  # never let the bar break the app
                log.debug("title bar not coloured: %s", e)
        return done

    def on_shown(self, *_args: Any) -> None:
        self.paint()
