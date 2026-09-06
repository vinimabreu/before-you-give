"""Cases from the pre-publication audit: leading-zero EINs, short and foundation
forms, negative revenue, missing cost lines, and concurrency on the paid path."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import load, org

from before_you_give.app import Limiter, create_app
from before_you_give.format import money_spoken
from before_you_give.narration import script
from before_you_give.propublica import (
    Client,
    JsonCache,
    format_ein,
    normalize_ein,
    parse_organization,
)
from before_you_give.reading import read
from before_you_give.speech import Speech


def fact(reading, key):
    return next(f for f in reading.facts if f.key == key)


def keys(reading):
    return [f.key for f in reading.facts]


# ---- EINs that start with a zero ------------------------------------------------

def test_integer_ein_keeps_its_leading_zero():
    assert normalize_ein(46169825) == 46169825
    assert normalize_ein("46169825") == 46169825
    assert normalize_ein("04-6169825") == 46169825
    assert format_ein(46169825) == "04-6169825"


def test_app_opens_an_organization_whose_ein_starts_with_zero(tmp_path: Path):
    data = load("red_cross_530196605.json")
    data["organization"]["ein"] = 46169825

    def fetch(url: str) -> dict:
        assert url.endswith("/organizations/46169825.json"), url
        return data

    client = TestClient(create_app(Client(fetch=fetch), None, cache_dir=tmp_path))
    for form in ("46169825", "046169825", "04-6169825"):
        r = client.get(f"/api/org/{form}")
        assert r.status_code == 200, form
        assert r.json()["organization"]["ein_text"] == "04-6169825"


# ---- 990-EZ and 990-PF carry the same lines under other names --------------------

def test_short_form_filer_gets_reserves_sources_and_trend():
    r = read(org("food_pantry_990ez_833988191.json"))
    assert r.latest.form == "990-EZ"
    assert {"size", "balance", "reserves", "sources", "trend"} <= set(keys(r))
    assert fact(r, "reserves").display == "about 3.1 years"      # 155,746 / (49,885 / 12)
    assert fact(r, "sources").meaning.startswith("Revenue came 100% from fees for its services")
    assert any("990-EZ" in c and "unreported rather than zero" in c for c in r.caveats)


def test_private_foundation_reads_net_worth_officer_pay_and_gifts(foundation):
    r = read(foundation)
    assert r.status == "foundation"
    assert fact(r, "reserves").display == "about 8 years"         # 70.4B / (8.84B / 12)
    assert "$13.6 million went to officers" in fact(r, "people").meaning
    assert fact(r, "sources").meaning.startswith("Revenue came 100% from donations and grants")
    assert any("grants paid out" in c for c in r.caveats)
    assert not any("not reported in this feed" in c for c in r.caveats)


# ---- signs and missing lines ------------------------------------------------------

def test_negative_revenue_is_spoken_with_its_sign(red_cross):
    assert money_spoken(-2_000_000) == "minus 2 million dollars"
    latest = replace(red_cross.latest, revenue=-2_000_000)
    r = read(replace(red_cross, filings=(latest,) + red_cross.filings[1:]))
    assert "minus 2 million dollars in revenue" in fact(r, "size").spoken
    assert "-$2 million in" in fact(r, "size").display
    assert "$" not in script(r) and "%" not in script(r)


def test_events_fact_needs_the_cost_line(red_cross):
    latest = replace(red_cross.latest, fundraising_direct_costs=None)
    r = read(replace(red_cross, filings=(latest,) + red_cross.filings[1:]))
    assert "events" not in keys(r)


def test_response_without_an_organization_is_an_error_not_a_crash(tmp_path: Path):
    with pytest.raises(ValueError):
        parse_organization({})
    client = TestClient(create_app(Client(fetch=lambda url: {}), None, cache_dir=tmp_path))
    assert client.get("/api/org/530196605").status_code == 502


# ---- concurrency on the paid path ----------------------------------------------------

def _hammer(fn, n: int) -> list[BaseException]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - collected for the assertion
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_same_text_from_many_threads_costs_one_call(tmp_path: Path):
    calls: list[str] = []
    lock = threading.Lock()

    def fetch(voice: str, text: str) -> bytes:
        with lock:
            calls.append(text)
        return b"mp3"

    s = Speech(tmp_path, fetch=fetch, voice_id="v", model="m")
    errors = _hammer(lambda: s.synthesize("the same script"), 25)
    assert errors == [] and calls == ["the same script"] and s.calls == 1


def test_cap_holds_under_concurrent_distinct_requests(tmp_path: Path):
    calls: list[str] = []
    lock = threading.Lock()

    def fetch(voice: str, text: str) -> bytes:
        with lock:
            calls.append(text)
        return b"mp3"

    s = Speech(tmp_path, fetch=fetch, voice_id="v", model="m", max_calls=2, max_chars=10_000)
    counter = iter(range(1000))
    errors = _hammer(lambda: s.synthesize(f"script {next(counter)}"), 20)
    assert len(calls) == 2 and s.calls == 2
    assert len(errors) == 18 and all(type(e).__name__ == "SpendingCapReached" for e in errors)


def test_limiter_is_exact_under_threads():
    limiter = Limiter(limit=5, window_seconds=60)
    allowed: list[bool] = []
    lock = threading.Lock()

    def hit() -> None:
        ok = limiter.allow("1.2.3.4", now=100.0)
        with lock:
            allowed.append(ok)

    _hammer(hit, 40)
    assert sum(allowed) == 5


# ---- the search cache on disk --------------------------------------------------------

def test_cache_keys_are_hashed_and_unreadable_entries_are_misses(tmp_path: Path):
    cache = JsonCache(tmp_path)
    cache.put("search_café_", {"a": 1})
    cache.put("search_cafè_", {"a": 2})
    assert cache.get("search_café_") == {"a": 1} and cache.get("search_cafè_") == {"a": 2}
    path = cache._path("broken")
    path.write_bytes(b"\xff\xfe not json")
    assert cache.get("broken") is None
    outside = cache._path("../../etc/passwd")
    assert outside.parent == tmp_path


def test_cache_sweeps_expired_entries_past_the_size_limit(tmp_path: Path):
    cache = JsonCache(tmp_path, ttl_seconds=0.0, max_files=2)
    for i in range(3):
        cache.put(f"k{i}", {"i": i})
    assert cache.sweep(force=True) >= 0
    assert len(list(tmp_path.glob("*.json"))) <= 2


def test_feed_failures_are_502_not_500(tmp_path: Path):
    import urllib.error

    def refuse(url: str) -> dict:
        if "search.json" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)  # type: ignore[arg-type]

    client = TestClient(create_app(Client(fetch=refuse), None, cache_dir=tmp_path))
    r = client.get("/api/search", params={"q": "red cross"})
    assert r.status_code == 502 and "try again" in r.json()["detail"]
    assert client.get("/api/org/530196605").status_code == 502


def test_missing_organization_is_404(tmp_path: Path):
    import urllib.error

    def missing(url: str) -> dict:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    client = TestClient(create_app(Client(fetch=missing), None, cache_dir=tmp_path))
    assert client.get("/api/org/530196605").status_code == 404


# ---- surface -------------------------------------------------------------------------

def test_head_works_and_api_docs_are_off(tmp_path: Path):
    client = TestClient(create_app(Client(fetch=lambda url: {}), None, cache_dir=tmp_path))
    assert client.head("/").status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_ez_fixture_carries_no_person_names():
    path = Path(__file__).parent / "fixtures" / "food_pantry_990ez_833988191.json"
    data = json.loads(path.read_text())
    assert not data["organization"].get("careofname")
