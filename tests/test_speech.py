from __future__ import annotations

from pathlib import Path

import pytest

from before_you_give.speech import Speech, SpendingCapReached, maybe_speech


def test_cache_means_the_same_text_costs_once(tmp_path: Path):
    calls: list[str] = []

    def fetch(voice: str, text: str) -> bytes:
        calls.append(text)
        return b"mp3" + text.encode()

    s = Speech(tmp_path, fetch=fetch, voice_id="v", model="m")
    assert s.synthesize("hello") == b"mp3hello"
    assert s.synthesize("  hello ") == b"mp3hello"
    assert calls == ["hello"] and s.calls == 1 and s.chars == 5
    assert s.cached("hello")


def test_cap_refuses_before_spending(tmp_path: Path):
    calls: list[str] = []
    s = Speech(tmp_path, fetch=lambda v, t: calls.append(t) or b"x", voice_id="v", model="m",
               max_calls=1, max_chars=1000)
    s.synthesize("one")
    with pytest.raises(SpendingCapReached):
        s.synthesize("two")
    assert calls == ["one"]
    assert s.synthesize("one") == b"x"   # cached text is still served past the cap


def test_char_cap_counts_the_request_before_sending(tmp_path: Path):
    s = Speech(tmp_path, fetch=lambda v, t: b"x", voice_id="v", model="m",
               max_calls=10, max_chars=5)
    with pytest.raises(SpendingCapReached):
        s.synthesize("six chars")


def test_without_key_speech_is_off(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert maybe_speech(tmp_path) is None


def test_empty_text_costs_nothing(tmp_path: Path):
    s = Speech(tmp_path, fetch=lambda v, t: b"x", voice_id="v", model="m")
    assert s.synthesize("   ") == b"" and s.calls == 0
