"""全局热键：直接调用 Win32 RegisterHotKey，配合 Qt 的原生事件过滤器接收 WM_HOTKEY。

Qt 自带的快捷键只在窗口有焦点时生效，全局热键必须走系统 API。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
    "backspace": 0x08, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def parse_combo(combo: str) -> tuple[int, int, str]:
    """把 "ctrl+alt+f" 解析成 (modifiers, virtual_key, 可读文本)。失败返回 (0,0,combo)。"""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        return 0, 0, combo
    mods = 0
    for p in parts[:-1]:
        if p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p in ("alt", "menu"):
            mods |= MOD_ALT
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p in ("win", "super", "meta"):
            mods |= MOD_WIN
    last = parts[-1]
    vk = 0
    if len(last) == 2 and last[0] == "f" and last[1:].isdigit():
        n = int(last[1:])
        if 1 <= n <= 24:
            vk = 0x6F + n
    elif len(last) == 1:
        vk = ord(last.upper())
    else:
        vk = NAMED_KEYS.get(last, 0)
    return mods, vk, combo


def register(hwnd: int, combo: str, hotkey_id: int = 1) -> tuple[bool, int]:
    """注册全局热键。返回 (是否成功, GetLastError)。"""
    mods, vk, _ = parse_combo(combo)
    if not vk:
        return False, 0
    ctypes.windll.kernel32.SetLastError(0)
    ok = ctypes.windll.user32.RegisterHotKey(
        wintypes.HWND(hwnd), ctypes.c_int(hotkey_id), ctypes.c_uint(mods), ctypes.c_uint(vk)
    )
    return bool(ok), ctypes.windll.kernel32.GetLastError()


def unregister(hwnd: int, hotkey_id: int = 1) -> None:
    try:
        ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(hwnd), ctypes.c_int(hotkey_id))
    except Exception:
        pass


class HotkeyFilter:
    """安装到 QApplication 的原生事件过滤器。需传入窗口 winId()。"""

    def __init__(self):
        from PySide6 import QtCore

        class _Filter(QtCore.QAbstractNativeEventFilter):
            def __init__(self, cb, hid):
                super().__init__()
                self.cb = cb
                self.hid = hid

            def nativeEventFilter(self, eventType, message):  # noqa: N802
                try:
                    if eventType in ("windows_generic_MSG", "windows_dispatcher_MSG"):
                        try:
                            addr = int(message)
                        except Exception:
                            addr = int(message.__int__())
                        msg = ctypes.cast(addr, ctypes.POINTER(MSG)).contents
                        if msg.message == WM_HOTKEY and int(msg.wParam) == self.hid:
                            self.cb()
                            return True, 0
                except Exception:
                    pass
                return False, 0

        self._impl_cls = _Filter
        self._impl = None
        self._app = None

    def install(self, app, hotkey_id: int, callback) -> None:
        self._impl = self._impl_cls(callback, hotkey_id)
        self._app = app
        app.installNativeEventFilter(self._impl)

    def uninstall(self) -> None:
        if self._app is not None and self._impl is not None:
            try:
                self._app.removeNativeEventFilter(self._impl)
            except Exception:
                pass
