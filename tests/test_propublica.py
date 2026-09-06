from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests.conftest import load

from before_you_give.propublica import (
    Client,
    JsonCache,
    format_ein,
    normalize_ein,
    parse_search,
    period_label,
)


def test_red_cross_parses_thirteen_filings_newest_first(red_cross):
    assert len(red_cross.filings) == 13
    assert [f.year for f in red_cross.filings[:3]] == [2023, 2022, 2021]
    latest = red_cross.latest
    assert latest.form == "990"
    assert latest.period_text == "June 2023"
    assert latest.revenue == 3_217_077_611
    assert latest.expenses == 2_971_106_889
    assert latest.net_assets == 3_019_994_931
    assert red_cross.newer_filings_without_data == 11
    assert red_cross.name == "American National Red Cross"
    assert red_cross.city == "Washington" and red_cross.state == "DC"


def test_private_foundation_keeps_missing_fields_missing(foundation):
    latest = foundation.latest
    assert latest.form == "990-PF"
    assert latest.net_assets is None
    assert latest.contributions is None
    assert latest.officer_comp is None
    assert latest.revenue == 7_810_137_842


def test_organization_with_no_filings(empty):
    assert empty.filings == ()
    assert empty.latest is None
    assert empty.newer_filings_without_data == 0


def test_search_parses_hits():
    hits = parse_search(load("search_doctors_without_borders.json"))
    assert len(hits) == 3
    assert hits[0].ein == 993999711
    assert hits[0].ein_text == "99-3999711"
    assert hits[0].state == "MO"


@pytest.mark.parametrize("raw,expected", [
    ("53-0196605", 530196605), ("530196605", 530196605), (530196605, 530196605),
    (" 13 3433452 ", 133433452),
])
def test_normalize_ein(raw, expected):
    assert normalize_ein(raw) == expected


def test_normalize_ein_rejects_wrong_length():
    with pytest.raises(ValueError):
        normalize_ein("1234")


def test_format_ein_pads_leading_zero():
    assert format_ein(50455719) == "05-0455719"


@pytest.mark.parametrize("period,label", [
    ("202306", "June 2023"), ("202412", "December 2024"), ("2023", "2023"), ("202399", "202399"),
])
def test_period_label(period, label):
    assert period_label(period) == label


def test_client_uses_cache_and_counts_fetches(tmp_path: Path):
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return load("red_cross_530196605.json")

    client = Client(fetch=fetch, cache=JsonCache(tmp_path))
    a = client.organization("53-0196605")
    b = client.organization(530196605)
    assert a.name == b.name
    assert len(calls) == 1 and client.fetches == 1
    assert calls[0].endswith("/organizations/530196605.json")


def test_cache_expires(tmp_path: Path):
    cache = JsonCache(tmp_path, ttl_seconds=0.01)
    cache.put("k", {"a": 1})
    assert cache.get("k") == {"a": 1}
    time.sleep(0.02)
    assert cache.get("k") is None


def test_search_builds_state_filter_and_trims_query():
    seen: list[str] = []

    def fetch(url: str) -> dict:
        seen.append(url)
        return load("search_doctors_without_borders.json")

    client = Client(fetch=fetch)
    client.search("  doctors   without borders ", state="ny")
    assert "q=doctors+without+borders" in seen[0]
    assert "state%5Bid%5D=NY" in seen[0]
    assert client.search("   ") == [] and len(seen) == 1
