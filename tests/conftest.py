from __future__ import annotations

import json
from pathlib import Path

import pytest

from before_you_give.propublica import Organization, parse_organization

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def org(name: str) -> Organization:
    return parse_organization(load(name))


@pytest.fixture
def red_cross() -> Organization:
    return org("red_cross_530196605.json")


@pytest.fixture
def msf() -> Organization:
    return org("msf_usa_133433452.json")


@pytest.fixture
def foundation() -> Organization:
    return org("foundation_990pf_562618866.json")


@pytest.fixture
def pantry() -> Organization:
    return org("food_pantry_990_264757945.json")


@pytest.fixture
def empty() -> Organization:
    return org("no_filings_874747104.json")
