"""Reliable native keyboard input without changing the user's clipboard."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


class Keyboard(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.WORD),
        ("scan", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class Mouse(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]


KEYS = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "option": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "meta": 0x5B,
    "command": 0x5B,
    "super": 0x5B,
    "lwin": 0x5B,
    "rwin": 0x5C,
    "lctrl": 0xA2,
    "rctrl": 0xA3,
    "lshift": 0xA0,
    "rshift": 0xA1,
    "lalt": 0xA4,
    "ralt": 0xA5,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 9,
    "back": 8,
    "backspace": 8,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "capslock": 0x14,
    "capital": 0x14,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "scroll": 0x91,
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "pause": 0x13,
    "apps": 0x5D,
    "menu": 0x5D,
}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})
KEYS.update({f"numpad{i}": 0x60 + i for i in range(10)})
EXTENDED = {
    0x21,
    0x22,
    0x23,
    0x24,
    0x25,
    0x26,
    0x27,
    0x28,
    0x2D,
    0x2E,
    0x5B,
    0x5C,
    0x5D,
    0xA3,
    0xA5,
    0x2C,
    0x90,
}
USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
USER32.SendInput.restype = wintypes.UINT
USER32.VkKeyScanW.argtypes = [wintypes.WCHAR]
USER32.VkKeyScanW.restype = ctypes.c_short


def key_event(vk=0, scan=0, flags=0):
    event = Input()
    event.type = 1
    event.value.keyboard = Keyboard(vk, scan, flags, 0, 0)
    return event


def send(events):
    if not events:
        return
    batch = (Input * len(events))(*events)
    sent = USER32.SendInput(len(batch), batch, ctypes.sizeof(Input))
    if sent != len(batch):
        # A partial chord must not leave our modifiers pressed. Release only keys
        # whose down events Windows accepted, preserving Unicode/extended flags.
        pressed = {}
        for event in events[:sent]:
            key = event.value.keyboard
            identity = (key.vk, key.scan, key.flags & ~2)
            if key.flags & 2:
                pressed.pop(identity, None)
            else:
                pressed[identity] = None
        releases = [key_event(vk, scan, flags | 2) for vk, scan, flags in reversed(pressed)]
        if releases:
            recovery = (Input * len(releases))(*releases)
            USER32.SendInput(len(recovery), recovery, ctypes.sizeof(Input))
        raise RuntimeError(
            f"Windows accepted {sent}/{len(batch)} input events. Check foreground desktop and elevation."
        )


def chord_events(shortcut: str):
    parts = [part.strip().lower() for part in shortcut.split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError("Use key names separated by +; use 'plus' for the + character")
    keys = []
    for part in parts:
        if part in KEYS:
            keys.append(KEYS[part])
        elif len(part) == 1 and part.isascii() and part.isalnum():
            keys.append(ord(part.upper()))
        else:
            char = "+" if part == "plus" else ("-" if part == "minus" else part)
            if len(char) != 1:
                raise ValueError(f"Unknown shortcut key: {part}")
            mapped = USER32.VkKeyScanW(char)
            if mapped == -1:
                raise ValueError(f"No key mapping for {part} in the current keyboard layout")
            for mask, modifier in ((1, 0x10), (2, 0x11), (4, 0x12)):
                if (mapped >> 8) & mask:
                    keys.append(modifier)
            keys.append(mapped & 0xFF)
    keys = list(dict.fromkeys(keys))
    return [key_event(vk=k, flags=int(k in EXTENDED)) for k in keys] + [
        key_event(vk=k, flags=int(k in EXTENDED) | 2) for k in reversed(keys)
    ]


def shortcut(self, shortcut: str):
    send(chord_events(shortcut))
    time.sleep(0.05)


def unicode_events(text: str):
    encoded = text.encode("utf-16-le")
    events = []
    for index in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[index : index + 2], "little")
        events.extend([key_event(scan=unit, flags=4), key_event(scan=unit, flags=6)])
    return events


def type_text(self, loc, text: str, caret_position="idle", clear=False, press_enter=False):
    self.click(loc=loc)
    if caret_position in {"start", "end"}:
        shortcut(self, "home" if caret_position == "start" else "end")
    if clear is True or (isinstance(clear, str) and clear.lower() == "true"):
        # Ctrl+A is not implemented by every edit control. These document-selection
        # keys also work in native Windows EDIT controls and multiline text fields.
        shortcut(self, "ctrl+home")
        shortcut(self, "ctrl+shift+end")
        shortcut(self, "backspace")
    # Literal UTF-16, including surrogate pairs, bypasses layout and SendKeys markup.
    # Newline/tab retain the upstream tool's keyboard behavior.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    buffer = []
    for character in text:
        if character in "\n\t":
            send(buffer)
            buffer.clear()
            shortcut(self, "enter" if character == "\n" else "tab")
        else:
            buffer.extend(unicode_events(character))
            if len(buffer) >= 128:
                send(buffer)
                buffer.clear()
                time.sleep(0.01)
    send(buffer)
    time.sleep(0.05)
    if press_enter is True or (isinstance(press_enter, str) and press_enter.lower() == "true"):
        shortcut(self, "enter")


def install():
    from windows_mcp.desktop.service import Desktop

    Desktop.shortcut = shortcut
    Desktop.type = type_text
