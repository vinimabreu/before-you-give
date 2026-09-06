from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import load

from before_you_give.app import Limiter, create_app
from before_you_give.propublica import Client
from before_you_give.speech import Speech

FILES = {
    "530196605": "red_cross_530196605.json",
    "562618866": "foundation_990pf_562618866.json",
    "874747104": "no_filings_874747104.json",
}


def fake_fetch(url: str) -> dict:
    if "search.json" in url:
        return load("search_doctors_without_borders.json")
    for ein, name in FILES.items():
        if url.endswith(f"/organizations/{ein}.json"):
            return load(name)
    raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]


@pytest.fixture
def api(tmp_path: Path):
    speech = Speech(tmp_path / "audio", fetch=lambda v, t: b"ID3fake", voice_id="v", model="m",
                    max_calls=2, max_chars=100_000)
    app = create_app(Client(fetch=fake_fetch), speech, cache_dir=tmp_path,
                     limiter=Limiter(limit=1, window_seconds=3600))
    return TestClient(app), speech


def test_index_and_health(api):
    client, _ = api
    assert client.get("/").status_code == 200
    assert "before you give" in client.get("/").text.lower()
    assert client.get("/healthz").json()["audio"] is True


def test_search(api):
    client, _ = api
    r = client.get("/api/search", params={"q": "doctors without borders"})
    assert r.status_code == 200
    assert r.json()["hits"][0]["ein_text"] == "99-3999711"
    assert client.get("/api/search", params={"q": "d"}).status_code == 422
    assert client.get("/api/search", params={"q": "food", "state": "RI1"}).status_code == 400


def test_organization_payload(api):
    client, _ = api
    r = client.get("/api/org/53-0196605")
    assert r.status_code == 200
    body = r.json()
    assert body["organization"]["name"] == "American National Red Cross"
    assert body["latest"]["period_text"] == "June 2023"
    assert [f["key"] for f in body["facts"]][:3] == ["size", "balance", "reserves"]
    assert body["years"][0]["year"] == 2011 and body["years"][-1]["deficit"] is False
    assert body["script"].startswith("Here is what")
    assert body["audio_available"] is True and body["audio_cached"] is False


def test_bad_and_unknown_ein(api):
    client, _ = api
    assert client.get("/api/org/12").status_code == 400
    assert client.get("/api/org/111111111").status_code == 404


def test_audio_is_cached_then_limited(api):
    client, speech = api
    first = client.get("/api/org/530196605/audio.mp3")
    assert first.status_code == 200 and first.content == b"ID3fake"
    assert first.headers["content-type"].startswith("audio/mpeg")
    again = client.get("/api/org/530196605/audio.mp3")
    assert again.status_code == 200 and speech.calls == 1        # cache, no limiter hit
    assert client.get("/api/org/530196605").json()["audio_cached"] is True
    blocked = client.get("/api/org/562618866/audio.mp3")          # 2nd new narration, limit 1/h
    assert blocked.status_code == 429


def test_audio_off_without_speech(tmp_path: Path):
    app = create_app(Client(fetch=fake_fetch), None, cache_dir=tmp_path)
    client = TestClient(app)
    r = client.get("/api/org/530196605/audio.mp3")
    assert r.status_code == 503
    assert client.get("/api/org/530196605").json()["audio_available"] is False


def test_spending_cap_surfaces_as_429(tmp_path: Path):
    speech = Speech(tmp_path / "audio", fetch=lambda v, t: b"x", voice_id="v", model="m",
                    max_calls=0)
    app = create_app(Client(fetch=fake_fetch), speech, cache_dir=tmp_path,
                     limiter=Limiter(limit=100))
    r = TestClient(app).get("/api/org/530196605/audio.mp3")
    assert r.status_code == 429 and "budget" in r.json()["detail"]
