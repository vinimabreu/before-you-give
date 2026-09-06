from __future__ import annotations

import pytest

from before_you_give.format import (
    money_display,
    money_spoken,
    months_display,
    pct_display,
    pct_spoken,
)


@pytest.mark.parametrize("n,display,spoken", [
    (3_217_077_611, "$3.2 billion", "3.2 billion dollars"),
    (1_000_000_000, "$1 billion", "1 billion dollars"),
    (1_529_383, "$1.5 million", "1.5 million dollars"),
    (919_126_379, "$919.1 million", "919.1 million dollars"),
    (194_573, "$195K", "195 thousand dollars"),
    (64_049, "$64,049", "64,049 dollars"),
    (0, "$0", "0 dollars"),
    (-2_000_000, "-$2 million", "minus 2 million dollars"),
])
def test_money(n, display, spoken):
    assert money_display(n) == display
    assert money_spoken(n) == spoken


def test_spoken_money_never_carries_signs():
    for n in (12, 12_345, 1_234_567, 12_345_678_901):
        assert "$" not in money_spoken(n) and "%" not in money_spoken(n)


@pytest.mark.parametrize("ratio,display,spoken", [
    (0.4, "40%", "40 percent"),
    (0.004, "under 1%", "under 1 percent"),
    (1.0, "100%", "100 percent"),
])
def test_pct(ratio, display, spoken):
    assert pct_display(ratio) == display
    assert pct_spoken(ratio) == spoken


@pytest.mark.parametrize("months,text", [
    (0.4, "less than a month"), (12.2, "about 12 months"), (23.6, "about 24 months"),
    (36.0, "about 3 years"), (41.0, "about 3.4 years"),
])
def test_months(months, text):
    assert months_display(months) == text
