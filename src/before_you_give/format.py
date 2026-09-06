"""Two renderings of every number: one for the eye, one for the voice.

The spoken form never carries a currency sign or a percent sign, because a
text to speech engine reads "$3.2B" five different ways and none of them is
what a listener wants.
"""
from __future__ import annotations


def money_display(n: int) -> str:
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000_000:
        return f"{sign}${_trim(a / 1e9)} billion"
    if a >= 1_000_000:
        return f"{sign}${_trim(a / 1e6)} million"
    if a >= 100_000:
        return f"{sign}${round(a / 1000)}K"
    return f"{sign}${a:,}"


def money_spoken(n: int) -> str:
    return ("minus " if n < 0 else "") + _money_spoken_abs(abs(n))


def _money_spoken_abs(a: int) -> str:
    if a >= 1_000_000_000:
        return f"{_trim(a / 1e9)} billion dollars"
    if a >= 1_000_000:
        return f"{_trim(a / 1e6)} million dollars"
    if a >= 100_000:
        return f"{round(a / 1000)} thousand dollars"
    return f"{a:,} dollars"


def pct_display(ratio: float) -> str:
    return f"{_pct(ratio)}%"


def pct_spoken(ratio: float) -> str:
    return f"{_pct(ratio)} percent"


def months_display(months: float) -> str:
    return _months(months)


def months_spoken(months: float) -> str:
    return _months(months)


def _pct(ratio: float) -> str:
    p = ratio * 100
    if abs(p) < 1:
        return "under 1"
    return str(round(p))


def _months(months: float) -> str:
    if months < 1:
        return "less than a month"
    if months < 24:
        return f"about {round(months)} months"
    return f"about {_trim(months / 12)} years"


def _trim(x: float) -> str:
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s
