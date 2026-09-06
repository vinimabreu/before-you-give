"""ProPublica Nonprofit Explorer client: two endpoints, a disk cache, typed rows.

The feed is IRS Form 990 extract data republished by ProPublica. Every number
this package shows comes from here unchanged; the package only reads it back
and never invents a field the feed did not carry.

Terms: https://projects.propublica.org/datastore/terms/ (cite ProPublica, do
not charge for access, do not redistribute the raw data).
"""
from __future__ import annotations

import json
import re
import time
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
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) != 9:
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
        revenue=_money(raw.get("totrevenue")),
        expenses=_money(raw.get("totfuncexpns")),
        assets=_money(raw.get("totassetsend")),
        liabilities=_money(raw.get("totliabend")),
        net_assets=_money(raw.get("totnetassetend")),
        officer_comp=_money(raw.get("compnsatncurrofcr")),
        other_salaries=_money(raw.get("othrsalwages")),
        payroll_tax=_money(raw.get("payrolltx")),
        contributions=_money(raw.get("totcntrbgfts")),
        program_revenue=_money(raw.get("totprgmrevnue")),
        investment_income=_money(raw.get("invstmntinc")),
        pro_fundraising_fees=_money(raw.get("profndraising")),
        fundraising_gross=_money(raw.get("grsincfndrsng")),
        fundraising_direct_costs=_money(raw.get("lessdirfndrsng")),
        pdf_url=raw.get("pdf_url") or None,
    )


def parse_organization(data: dict[str, Any]) -> Organization:
    org = data.get("organization") or {}
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
    """One JSON file per key, with a TTL. Filings change once a year; a week is plenty."""

    def __init__(self, directory: Path, ttl_seconds: float = 7 * 24 * 3600) -> None:
        self.directory = directory
        self.ttl = ttl_seconds
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        return self.directory / f"{safe}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


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
        return parse_search(self._get(key, url))

    def organization(self, ein: str | int) -> Organization:
        n = normalize_ein(ein)
        return parse_organization(self._get(f"org_{n}", f"{BASE_URL}/organizations/{n}.json"))
