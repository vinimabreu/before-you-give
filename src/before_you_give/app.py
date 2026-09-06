"""HTTP surface: one page, three JSON routes, one audio route."""
from __future__ import annotations

import os
import threading
import time
import urllib.error
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from before_you_give import __version__
from before_you_give.narration import script
from before_you_give.propublica import Client, JsonCache, Organization, normalize_ein
from before_you_give.reading import Reading, read
from before_you_give.speech import Speech, SpendingCapReached, maybe_speech

WEB_DIR = Path(__file__).parent / "web"


class Limiter:
    """Per-address ceiling on audio synthesis: N new syntheses per window."""

    def __init__(self, limit: int = 6, window_seconds: float = 3600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._hits) > 10_000:   # forget addresses whose window has passed
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
                    del self._hits[k]
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def reading_payload(reading: Reading) -> dict[str, Any]:
    org: Organization = reading.org
    latest = reading.latest
    return {
        "organization": {
            "ein": org.ein,
            "ein_text": org.ein_text,
            "name": org.name,
            "city": org.city,
            "state": org.state,
            "ntee_code": org.ntee_code,
            "subsection": org.subsection,
            "propublica_url": org.propublica_url,
        },
        "status": reading.status,
        "latest": None if latest is None else {
            "year": latest.year,
            "period_text": latest.period_text,
            "form": latest.form,
            "pdf_url": latest.pdf_url,
        },
        "facts": [asdict(f) for f in reading.facts],
        "years": [
            {**asdict(y), "deficit": y.deficit} for y in reading.years
        ],
        "caveats": list(reading.caveats),
        "script": script(reading),
    }


def client_address(request: Request, trust_proxy: bool) -> str:
    """Behind nginx or Vercel the proxy writes the real address into X-Real-IP."""
    if trust_proxy:
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def create_app(
    client: Client | None = None,
    speech: Speech | None = None,
    *,
    cache_dir: Path | None = None,
    limiter: Limiter | None = None,
    trust_proxy: bool | None = None,
) -> FastAPI:
    cache_dir = cache_dir or Path(".cache")
    if trust_proxy is None:
        trust_proxy = os.environ.get("BYG_TRUST_PROXY", "").strip() in ("1", "true", "yes")
    client = client or Client(cache=JsonCache(cache_dir / "propublica"))
    speech = speech if speech is not None else maybe_speech(cache_dir / "audio")
    limiter = limiter or Limiter()
    app = FastAPI(
        title="before-you-give", version=__version__,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def _reading(ein: str) -> Reading:
        try:
            n = normalize_ein(ein)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            org = client.organization(n)
        except ValueError as e:   # the feed answered, but not with an organization
            raise HTTPException(502, f"unexpected response from the data feed: {e}") from e
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise HTTPException(404, f"no organization found for EIN {ein}") from e
            raise HTTPException(502, "the data feed did not answer; try again in a moment") from e
        except Exception as e:    # network, timeout
            raise HTTPException(502, "the data feed did not answer; try again in a moment") from e
        return read(org)

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def index() -> str:
        return index_html

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "audio": speech is not None,
            "tts_calls": speech.calls if speech else 0,
        }

    @app.get("/api/search")
    def search(
        q: str = Query(min_length=2, max_length=120), state: str | None = None
    ) -> dict[str, Any]:
        if state is not None and (len(state) != 2 or not state.isalpha()):
            raise HTTPException(400, "state is a two letter code")
        try:
            hits = client.search(q, state)
        except Exception as e:   # the feed refused or timed out; say so instead of a bare 500
            raise HTTPException(502, "the data feed did not answer; try again in a moment") from e
        return {"hits": [
            {"ein": h.ein, "ein_text": h.ein_text, "name": h.name, "city": h.city,
             "state": h.state, "ntee_code": h.ntee_code, "subsection": h.subsection}
            for h in hits[:10]
        ]}

    @app.get("/api/org/{ein}")
    def organization(ein: str) -> dict[str, Any]:
        reading = _reading(ein)
        payload = reading_payload(reading)
        payload["audio_available"] = speech is not None
        payload["audio_cached"] = speech is not None and speech.cached(payload["script"])
        return payload

    @app.get("/api/org/{ein}/audio.mp3")
    def audio(ein: str, request: Request) -> Response:
        if speech is None:
            raise HTTPException(503, "audio is off on this server: no ELEVENLABS_API_KEY")
        reading = _reading(ein)
        text = script(reading)
        if not speech.cached(text):
            who = client_address(request, trust_proxy)
            if not limiter.allow(who):
                raise HTTPException(429, "too many new narrations from this address; try later")
        try:
            data = speech.synthesize(text)
        except SpendingCapReached as e:
            raise HTTPException(429, f"narration budget for this server is used up ({e})") from e
        return Response(content=data, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=604800"})

    return app


_default: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """`uvicorn before_you_give.app:app` builds the default app on first use, so
    importing this module touches neither the disk nor the environment."""
    global _default
    if name == "app":
        if _default is None:
            _default = create_app()
        return _default
    raise AttributeError(name)
