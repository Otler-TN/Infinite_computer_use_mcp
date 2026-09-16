import ctypes

import pytest
import windows_input
from windows_input import Input, chord_events, send, unicode_events


def test_input_struct_matches_windows_abi():
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_ctrl_a_uses_virtual_keys_with_correct_flags():
    events = chord_events("ctrl+a")
    assert [(e.value.keyboard.vk, e.value.keyboard.flags) for e in events] == [
        (0x11, 0),
        (0x41, 0),
        (0x41, 2),
        (0x11, 2),
    ]


def test_unicode_supplementary_characters_use_surrogate_pairs():
    events = unicode_events("é😀{}")
    down = [e.value.keyboard.scan for e in events if e.value.keyboard.flags == 4]
    assert down == [0xE9, 0xD83D, 0xDE00, ord("{"), ord("}")]
    assert all(event.value.keyboard.vk == 0 for event in events)


@pytest.mark.parametrize("keys", ["ctrl++a", "unknown-key", ""])
def test_invalid_chord_rejected_before_sending(keys):
    with pytest.raises(ValueError):
        chord_events(keys)


@pytest.mark.parametrize("accepted", [0, 2, 3])
def test_partial_input_releases_only_accepted_pressed_keys(monkeypatch, accepted):
    batches = []

    def fake_send(count, batch, size):
        batches.append([(event.value.keyboard.vk, event.value.keyboard.flags) for event in batch])
        return accepted if len(batches) == 1 else count

    monkeypatch.setattr(windows_input.USER32, "SendInput", fake_send)
    with pytest.raises(RuntimeError, match=f"accepted {accepted}/4"):
        send(chord_events("ctrl+a"))
    if accepted == 0:
        assert len(batches) == 1
    elif accepted == 2:
        assert batches[1] == [(0x41, 2), (0x11, 2)]
    else:
        assert batches[1] == [(0x11, 2)]
