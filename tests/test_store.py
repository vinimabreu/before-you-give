from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from before_you_give.speech import BLOB_API, BlobStore, DiskStore, Speech, SpendingCapReached


class FakeBlobService:
    """Enough of Vercel Blob's REST surface for the store: PUT, list by prefix, GET."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        self.calls.append((method, url))
        if url.startswith(BLOB_API):
            assert headers.get("authorization") == "Bearer tok"
            if method == "PUT":
                pathname = url[len(BLOB_API) + 1:]
                assert headers.get("x-add-random-suffix") == "0"
                self.blobs[pathname] = body or b""
                meta = {"url": f"https://x.public.blob/{pathname}", "pathname": pathname}
                return 200, json.dumps(meta).encode()
            if method == "GET":
                q = parse_qs(urlparse(url).query)
                prefix = q.get("prefix", [""])[0]
                items = [{"pathname": p, "url": f"https://x.public.blob/{p}"}
                         for p in self.blobs if p.startswith(prefix)]
                return 200, json.dumps({"blobs": items, "hasMore": False}).encode()
        if url.startswith("https://x.public.blob/"):
            pathname = url[len("https://x.public.blob/"):]
            return (200, self.blobs[pathname]) if pathname in self.blobs else (404, b"")
        raise AssertionError(f"unexpected {method} {url}")


def test_blob_store_round_trip_and_count():
    svc = FakeBlobService()
    store = BlobStore("tok", http=svc)
    assert store.get("a.mp3") is None and not store.exists("a.mp3") and store.count() == 0
    store.put("a.mp3", b"AAA")
    assert store.exists("a.mp3") and store.get("a.mp3") == b"AAA" and store.count() == 1
    assert not store.exists("a.mp3.other")   # prefix match is not an exact match
    assert "audio/a.mp3" in svc.blobs


def test_speech_on_blob_store_caps_by_narrations_stored():
    svc = FakeBlobService()
    store = BlobStore("tok", http=svc)
    fetched: list[str] = []
    s = Speech(store=store, fetch=lambda v, t: fetched.append(t) or b"mp3",
               voice_id="v", model="m", max_calls=2, max_chars=10_000)
    assert s.synthesize("one") == b"mp3" and s.synthesize("two") == b"mp3"
    with pytest.raises(SpendingCapReached):
        s.synthesize("three")
    assert s.synthesize("one") == b"mp3" and fetched == ["one", "two"]
    # a fresh process (new counters) still sees the two stored narrations
    fresh = Speech(store=BlobStore("tok", http=svc), fetch=lambda v, t: b"x",
                   voice_id="v", model="m", max_calls=2)
    with pytest.raises(SpendingCapReached):
        fresh.synthesize("four")
    assert fresh.cached("one")


def test_blob_failures_surface_as_errors_not_silent_spend():
    def broken(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 500, b"nope"

    store = BlobStore("tok", http=broken)
    with pytest.raises(RuntimeError):
        store.count()
    with pytest.raises(RuntimeError):
        store.put("a.mp3", b"x")


def test_disk_store_counts_only_narrations(tmp_path: Path):
    store = DiskStore(tmp_path / "audio")
    assert store.count() == 0 and store.get("a.mp3") is None
    store.put("a.mp3", b"A")
    (tmp_path / "audio" / "junk.txt").write_text("x")
    assert store.count() == 1 and store.get("a.mp3") == b"A" and store.exists("a.mp3")


def test_speech_without_store_or_dir_is_an_error():
    with pytest.raises(ValueError):
        Speech(fetch=lambda v, t: b"x")
