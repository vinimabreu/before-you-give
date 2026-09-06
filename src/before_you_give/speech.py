"""ElevenLabs narration behind a cache and a hard spending cap.

The cap and the cache exist before the first paid call, not after the first
bill. A synthesized script is stored under the hash of its text, so the same
organization never costs twice, and synthesis refuses to run past the
configured ceilings.

Two stores: a directory on disk for a server, and Vercel Blob for serverless,
where a function instance has no disk of its own to remember anything. On the
blob store the call ceiling is enforced against the number of narrations
already stored, which is the only counter every instance can see.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

FetchAudio = Callable[[str, str], bytes]
"""(voice_id, text) -> mp3 bytes. Injectable so tests never touch the network."""

HttpCall = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]
"""(method, url, headers, body) -> (status, body). Injectable for the blob store."""

DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
"""A premade ElevenLabs voice. Set BYG_VOICE_ID to use another."""
DEFAULT_MODEL = "eleven_turbo_v2_5"
DEFAULT_MAX_CALLS = 200
DEFAULT_MAX_CHARS = 200_000
BLOB_API = "https://blob.vercel-storage.com"


class SpendingCapReached(RuntimeError):
    pass


class SpeechDisabled(RuntimeError):
    pass


class AudioStore(Protocol):
    def get(self, name: str) -> bytes | None: ...
    def exists(self, name: str) -> bool: ...
    def put(self, name: str, data: bytes) -> None: ...
    def count(self) -> int | None:
        """Narrations stored so far, or None when the store cannot say."""
        ...


class DiskStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, name: str) -> Path:
        return self.directory / name

    def get(self, name: str) -> bytes | None:
        path = self._path(name)
        return path.read_bytes() if path.exists() else None

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def put(self, name: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
        os.replace(tmp_name, self._path(name))

    def count(self) -> int | None:
        if not self.directory.exists():
            return 0
        return sum(1 for _ in self.directory.glob("*.mp3"))


def _urllib_call(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class BlobStore:
    """Vercel Blob through its REST API: one blob per narration under a prefix."""

    def __init__(self, token: str, prefix: str = "audio/", http: HttpCall | None = None) -> None:
        self._token = token
        self.prefix = prefix
        self._http = http or _urllib_call

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}", "x-api-version": "7", **extra}

    def _list(self, prefix: str) -> list[dict[str, Any]]:
        blobs: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"prefix": prefix, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            status, body = self._http("GET", f"{BLOB_API}?{urllib.parse.urlencode(params)}",
                                      self._headers(), None)
            if status != 200:
                raise RuntimeError(f"blob list failed: HTTP {status}")
            page = json.loads(body.decode("utf-8"))
            blobs.extend(page.get("blobs") or [])
            cursor = page.get("cursor") if page.get("hasMore") else None
            if not cursor:
                return blobs

    def _find(self, name: str) -> dict[str, Any] | None:
        pathname = self.prefix + name
        for blob in self._list(pathname):
            if blob.get("pathname") == pathname:
                return blob
        return None

    def get(self, name: str) -> bytes | None:
        blob = self._find(name)
        if blob is None:
            return None
        status, body = self._http("GET", blob["url"], {}, None)
        return body if status == 200 else None

    def exists(self, name: str) -> bool:
        return self._find(name) is not None

    def put(self, name: str, data: bytes) -> None:
        status, body = self._http(
            "PUT", f"{BLOB_API}/{self.prefix}{name}",
            self._headers(**{"x-content-type": "audio/mpeg", "x-add-random-suffix": "0"}),
            data,
        )
        if status != 200:
            raise RuntimeError(f"blob put failed: HTTP {status} {body[:120]!r}")

    def count(self) -> int | None:
        return len(self._list(self.prefix))


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
        cache_dir: Path | None = None,
        *,
        store: AudioStore | None = None,
        voice_id: str | None = None,
        model: str | None = None,
        fetch: FetchAudio | None = None,
        max_calls: int | None = None,
        max_chars: int | None = None,
    ) -> None:
        if store is None:
            if cache_dir is None:
                raise ValueError("Speech needs a store or a cache_dir")
            store = DiskStore(cache_dir)
        self.store = store
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
        self._lock = threading.Lock()
        if fetch is not None:
            self._fetch = fetch
        else:
            key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not key:
                raise SpeechDisabled("ELEVENLABS_API_KEY is not set")
            self._fetch = _http_fetch(key, self.model)

    def _name(self, text: str) -> str:
        digest = hashlib.sha256(f"{self.model}|{self.voice_id}|{text}".encode()).hexdigest()
        return f"{digest}.mp3"

    def cached(self, text: str) -> bool:
        return self.store.exists(self._name(text.strip()))

    def _check_cap(self, text: str) -> None:
        stored = self.store.count()
        over_calls = self.calls >= self.max_calls or (
            stored is not None and stored >= self.max_calls
        )
        if over_calls or self.chars + len(text) > self.max_chars:
            raise SpendingCapReached(
                f"cap reached: {self.calls} calls this process, {stored} narrations stored, "
                f"limit {self.max_calls}; {self.chars}/{self.max_chars} characters"
            )

    def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            return b""
        name = self._name(text)
        hit = self.store.get(name)
        if hit is not None:
            return hit
        # One synthesis at a time. Ten people asking for the same organization in
        # the same second must cost one call, and the cap must hold under load.
        with self._lock:
            hit = self.store.get(name)
            if hit is not None:
                return hit
            self._check_cap(text)
            audio = self._fetch(self.voice_id, text)
            self.calls += 1
            self.chars += len(text)
            self.store.put(name, audio)
            return audio


def maybe_speech(cache_dir: Path) -> Speech | None:
    """Speech if a key is in the environment; None keeps the app text-only.

    With BLOB_READ_WRITE_TOKEN set (Vercel), narrations live in Vercel Blob;
    otherwise in cache_dir on disk.
    """
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    store: AudioStore = BlobStore(token) if token else DiskStore(cache_dir)
    try:
        return Speech(store=store)
    except SpeechDisabled:
        return None
