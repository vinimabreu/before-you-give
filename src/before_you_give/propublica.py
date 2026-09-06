"""ProPublica Nonprofit Explorer client: two endpoints, a disk cache, typed rows.

The feed is IRS Form 990 extract data republished by ProPublica. Every number
this package shows comes from here unchanged; the package only reads it back
and never invents a field the feed did not carry.

Terms: https://projects.propublica.org/datastore/terms/ (cite ProPublica, do
not charge for access, do not redistribute the raw data).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FetchJson = Callable[[str], dict[str, Any]]
"""url -> decoded JSON. Injectable so the whole suite runs offline."""

BASE_URL = "https://projects.propublica.org/nonprofits/api/v2"
USER_AGENT = "before-you-give/0.1 (+https://github.com/vinimabreu/before-you-give)"
FORM_NAMES = {0: "990", 1: "990-EZ", 2: "990-PF"}
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _http_fetch(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_ein(raw: str | int) -> int:
    """An EIN is nine digits, and the first one is often a zero (all of New England).

    Integers are accepted as they are, because the feed returns EINs as integers
    and 46169825 is 04-6169825 with the zero gone. A string of eight digits is
    read the same way; anything shorter is a typo, not a missing zero.
    """
    if isinstance(raw, bool):
        raise ValueError(f"not an EIN: {raw!r}")
    if isinstance(raw, int):
        if 0 < raw < 10**9:
            return raw
        raise ValueError(f"an EIN has 9 digits, got {raw!r}")
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) not in (8, 9) or int(digits) == 0:
        raise ValueError(f"an EIN has 9 digits, got {raw!r}")
    return int(digits)


def format_ein(ein: int) -> str:
    s = f"{ein:09d}"
    return f"{s[:2]}-{s[2:]}"


def period_label(period: str) -> str:
    """'202306' -> 'June 2023' (the month the fiscal year ended)."""
    if len(period) != 6 or not period.isdigit():
        return period
    month = int(period[4:])
    if not 1 <= month <= 12:
        return period
    return f"{MONTHS[month - 1]} {period[:4]}"


def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _money(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _first(raw: dict[str, Any], *keys: str) -> int | None:
    """The same line item lives under different keys on the 990, 990-EZ and 990-PF."""
    for key in keys:
        value = _money(raw.get(key))
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class Filing:
    year: int
    period: str
    form: str
    revenue: int | None
    expenses: int | None
    assets: int | None
    liabilities: int | None
    net_assets: int | None
    officer_comp: int | None
    other_salaries: int | None
    payroll_tax: int | None
    contributions: int | None
    program_revenue: int | None
    investment_income: int | None
    pro_fundraising_fees: int | None
    fundraising_gross: int | None
    fundraising_direct_costs: int | None
    pdf_url: str | None

    @property
    def period_text(self) -> str:
        return period_label(self.period)


@dataclass(frozen=True)
class Organization:
    ein: int
    name: str
    city: str
    state: str
    ntee_code: str | None
    subsection: int | None
    filings: tuple[Filing, ...]
    """Filings that carry numbers, newest first."""
    newer_filings_without_data: int
    """Filings ProPublica lists (usually newer) whose numbers are not in the feed yet."""

    @property
    def ein_text(self) -> str:
        return format_ein(self.ein)

    @property
    def latest(self) -> Filing | None:
        return self.filings[0] if self.filings else None

    @property
    def propublica_url(self) -> str:
        return f"https://projects.propublica.org/nonprofits/organizations/{self.ein}"


@dataclass(frozen=True)
class SearchHit:
    ein: int
    name: str
    city: str
    state: str
    ntee_code: str | None
    subsection: int | None

    @property
    def ein_text(self) -> str:
        return format_ein(self.ein)


def parse_filing(raw: dict[str, Any]) -> Filing:
    period = str(raw.get("tax_prd") or "")
    year = raw.get("tax_prd_yr")
    if not isinstance(year, int):
        year = int(period[:4]) if period[:4].isdigit() else 0
    form_code = raw.get("formtype")
    form = FORM_NAMES.get(form_code, str(raw.get("formtype_str") or "990"))
    return Filing(
        year=year,
        period=period,
        form=form,
        revenue=_first(raw, "totrevenue", "totrevnue"),
        expenses=_first(raw, "totfuncexpns", "totexpns"),
        assets=_first(raw, "totassetsend"),
        liabilities=_first(raw, "totliabend"),
        net_assets=_first(raw, "totnetassetend", "totnetassetsend", "tfundnworth"),
        officer_comp=_first(raw, "compnsatncurrofcr", "compofficers"),
        other_salaries=_first(raw, "othrsalwages", "salariesetc"),
        payroll_tax=_first(raw, "payrolltx"),
        contributions=_first(raw, "totcntrbgfts", "totcntrbs", "grscontrgifts"),
        program_revenue=_first(raw, "totprgmrevnue", "prgmservrev"),
        investment_income=_first(raw, "invstmntinc", "othrinvstinc", "netinvstinc"),
        pro_fundraising_fees=_first(raw, "profndraising"),
        fundraising_gross=_first(raw, "grsincfndrsng", "grsrevnuefndrsng"),
        fundraising_direct_costs=_first(raw, "lessdirfndrsng", "direxpns"),
        pdf_url=raw.get("pdf_url") or None,
    )


def parse_organization(data: dict[str, Any]) -> Organization:
    org = data.get("organization") or {}
    if not isinstance(org.get("ein"), int):
        raise ValueError("the response carries no organization")
    filings = [parse_filing(f) for f in data.get("filings_with_data") or []]
    filings.sort(key=lambda f: f.period, reverse=True)
    return Organization(
        ein=int(org.get("ein")),
        name=str(org.get("name") or "").strip(),
        city=str(org.get("city") or "").strip().title(),
        state=str(org.get("state") or "").strip(),
        ntee_code=org.get("ntee_code") or None,
        subsection=_int_or_none(org.get("subsection_code")),
        filings=tuple(filings),
        newer_filings_without_data=len(data.get("filings_without_data") or []),
    )


def parse_search(data: dict[str, Any]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for o in data.get("organizations") or []:
        try:
            ein = int(o["ein"])
        except (KeyError, TypeError, ValueError):
            continue
        hits.append(SearchHit(
            ein=ein,
            name=str(o.get("name") or "").strip(),
            city=str(o.get("city") or "").strip().title(),
            state=str(o.get("state") or "").strip(),
            ntee_code=o.get("ntee_code") or None,
            subsection=_int_or_none(o.get("subseccd")),
        ))
    return hits


class JsonCache:
    """One JSON file per key, with a TTL. Filings change once a year; a week is plenty.

    Every distinct search query becomes a file, so the directory is swept of
    expired entries whenever it grows past `max_files`. Keys are hashed into the
    file name: two queries that differ only in accents are two entries, and no
    key can reach outside the directory.
    """

    def __init__(
        self, directory: Path, ttl_seconds: float = 7 * 24 * 3600, max_files: int = 500
    ) -> None:
        self.directory = directory
        self.ttl = ttl_seconds
        self.max_files = max_files

    def _path(self, key: str) -> Path:
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", key)[:40]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.directory / f"{stem}.{digest}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # missing, unreadable, invalid bytes, bad JSON
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
        self.sweep()

    def sweep(self, force: bool = False) -> int:
        """Delete expired entries once the directory is bigger than max_files."""
        entries = list(self.directory.glob("*.json")) if self.directory.exists() else []
        if not force and len(entries) <= self.max_files:
            return 0
        now = time.time()
        removed = 0
        for entry in entries:
            try:
                if now - entry.stat().st_mtime > self.ttl:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


class Client:
    def __init__(self, fetch: FetchJson | None = None, cache: JsonCache | None = None) -> None:
        self._fetch = fetch or _http_fetch
        self._cache = cache
        self.fetches = 0

    def _get(self, key: str, url: str) -> dict[str, Any]:
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
        data = self._fetch(url)
        self.fetches += 1
        if self._cache is not None:
            self._cache.put(key, data)
        return data

    def search(self, query: str, state: str | None = None) -> list[SearchHit]:
        query = " ".join(query.split())
        if not query:
            return []
        params = [("q", query)]
        if state:
            params.append(("state[id]", state.strip().upper()))
        url = f"{BASE_URL}/search.json?{urllib.parse.urlencode(params)}"
        key = f"search_{query.lower()}_{(state or '').lower()}"
        try:
            return parse_search(self._get(key, url))
        except urllib.error.HTTPError as e:
            if e.code == 404:   # the feed answers "no results" with a 404
                return []
            raise

    def organization(self, ein: str | int) -> Organization:
        n = normalize_ein(ein)
        return parse_organization(self._get(f"org_{n}", f"{BASE_URL}/organizations/{n}.json"))
