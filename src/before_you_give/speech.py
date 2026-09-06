"""ElevenLabs narration with a disk cache and a hard spending cap.

The cap and the cache exist before the first paid call, not after the first
bill. A synthesized script is cached by its text, so the same organization
never costs twice, and the process refuses to synthesize past the configured
call and character ceilings.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

FetchAudio = Callable[[str, str], bytes]
"""(voice_id, text) -> mp3 bytes. Injectable so tests never touch the network."""

DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
"""A premade ElevenLabs voice. Set BYG_VOICE_ID to use another."""
DEFAULT_MODEL = "eleven_turbo_v2_5"
DEFAULT_MAX_CALLS = 200
DEFAULT_MAX_CHARS = 200_000


class SpendingCapReached(RuntimeError):
    pass


class SpeechDisabled(RuntimeError):
    pass


def _http_fetch(api_key: str, model: str) -> FetchAudio:
    def fetch(voice_id: str, text: str) -> bytes:
        body = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.6,
                "similarity_boost": 0.8,
                "style": 0.2,
                "use_speaker_boost": True,
                "speed": 0.95,
            },
        }
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    return fetch


class Speech:
    def __init__(
        self,
        cache_dir: Path,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        fetch: FetchAudio | None = None,
        max_calls: int | None = None,
        max_chars: int | None = None,
    ) -> None:
        self.voice_id = voice_id or os.environ.get("BYG_VOICE_ID", DEFAULT_VOICE_ID)
        self.model = model or os.environ.get("BYG_TTS_MODEL", DEFAULT_MODEL)
        self.max_calls = max_calls if max_calls is not None else int(
            os.environ.get("BYG_TTS_MAX_CALLS", DEFAULT_MAX_CALLS)
        )
        self.max_chars = max_chars if max_chars is not None else int(
            os.environ.get("BYG_TTS_MAX_CHARS", DEFAULT_MAX_CHARS)
        )
        self.calls = 0
        self.chars = 0
        self._cache = cache_dir
        self._cache.mkdir(parents=True, exist_ok=True)
        if fetch is not None:
            self._fetch = fetch
        else:
            key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not key:
                raise SpeechDisabled("ELEVENLABS_API_KEY is not set")
            self._fetch = _http_fetch(key, self.model)

    def _path(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self.model}|{self.voice_id}|{text}".encode()).hexdigest()
        return self._cache / f"{digest}.mp3"

    def cached(self, text: str) -> bool:
        return self._path(text.strip()).exists()

    def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            return b""
        path = self._path(text)
        if path.exists():
            return path.read_bytes()
        if self.calls >= self.max_calls or self.chars + len(text) > self.max_chars:
            raise SpendingCapReached(
                f"cap reached: {self.calls}/{self.max_calls} calls, "
                f"{self.chars}/{self.max_chars} characters"
            )
        audio = self._fetch(self.voice_id, text)
        self.calls += 1
        self.chars += len(text)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)
        return audio


def maybe_speech(cache_dir: Path) -> Speech | None:
    """Speech if a key is in the environment; None keeps the app text-only."""
    try:
        return Speech(cache_dir)
    except SpeechDisabled:
        return None
