"""Turn a filing into plain statements, each paired with what it cannot say.

There is no score in this module on purpose. A score would have to weigh
things the filing does not measure, and the weighing would be a guess dressed
as a number. What a filing supports is a sentence with a limit attached, so
that is the unit here: a Fact carries a value, a meaning, and a limit, and the
limit is not optional.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from before_you_give.format import (
    money_display,
    money_spoken,
    months_display,
    months_spoken,
    pct_display,
    pct_spoken,
)
from before_you_give.propublica import Filing, Organization

Status = Literal["ok", "foundation", "no_financials"]


@dataclass(frozen=True)
class Fact:
    key: str
    label: str
    display: str
    """Short value for the page, e.g. '$3.2 billion' or 'about 12 months'."""
    meaning: str
    """One plain sentence for the page."""
    spoken: str
    """The same sentence written for a voice: no signs, no abbreviations."""
    limit: str
    """What this number does not tell you. Never empty."""


@dataclass(frozen=True)
class YearRow:
    year: int
    period_text: str
    revenue: int | None
    expenses: int | None
    net_assets: int | None

    @property
    def deficit(self) -> bool | None:
        if self.revenue is None or self.expenses is None:
            return None
        return self.expenses > self.revenue


@dataclass(frozen=True)
class Reading:
    org: Organization
    status: Status
    latest: Filing | None
    facts: tuple[Fact, ...]
    years: tuple[YearRow, ...]
    """Oldest first, for the chart."""
    caveats: tuple[str, ...]
    """What the filings cannot tell you. Always present, even with no data."""


def _share(part: int | None, whole: int | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return part / whole


def read(org: Organization) -> Reading:
    latest = org.latest
    years = tuple(
        YearRow(f.year, f.period_text, f.revenue, f.expenses, f.net_assets)
        for f in sorted(org.filings, key=lambda f: f.period)
    )
    if latest is None:
        return Reading(org, "no_financials", None, (), years, _caveats(org, None, "no_financials"))
    status: Status = "foundation" if latest.form == "990-PF" else "ok"
    facts: list[Fact] = []
    facts.extend(_size(latest))
    facts.extend(_balance(latest))
    facts.extend(_reserves(latest))
    facts.extend(_people(latest))
    facts.extend(_sources(latest))
    facts.extend(_fundraising(latest))
    facts.extend(_trend(years))
    return Reading(org, status, latest, tuple(facts), years, _caveats(org, latest, status))


def _size(f: Filing) -> list[Fact]:
    if f.revenue is None or f.expenses is None:
        return []
    when = f.period_text
    return [Fact(
        key="size",
        label="Size",
        display=f"{money_display(f.revenue)} in, {money_display(f.expenses)} out",
        meaning=(
            f"In the fiscal year ending {when}, it reported {money_display(f.revenue)} "
            f"in revenue and {money_display(f.expenses)} in expenses."
        ),
        spoken=(
            f"In the fiscal year ending {when}, it reported {money_spoken(f.revenue)} "
            f"in revenue and {money_spoken(f.expenses)} in expenses."
        ),
        limit="Size says how much money moved, not how well it was used.",
    )]


def _balance(f: Filing) -> list[Fact]:
    if f.revenue is None or f.expenses is None:
        return []
    diff = f.revenue - f.expenses
    ratio = _share(abs(diff), f.expenses)
    pct = f", about {pct_display(ratio)} of what it spent" if ratio is not None else ""
    pct_s = f", about {pct_spoken(ratio)} of what it spent" if ratio is not None else ""
    if diff >= 0:
        meaning = f"It ended the year with {money_display(diff)} more than it spent{pct}."
        spoken = f"It ended the year with {money_spoken(diff)} more than it spent{pct_s}."
        display = f"{money_display(diff)} surplus"
    else:
        meaning = f"It spent {money_display(-diff)} more than it took in{pct}."
        spoken = f"It spent {money_spoken(-diff)} more than it took in{pct_s}."
        display = f"{money_display(-diff)} deficit"
    return [Fact(
        key="balance",
        label="Year result",
        display=display,
        meaning=meaning,
        spoken=spoken,
        limit=(
            "One year alone proves little. A deficit can be a planned draw on savings "
            "or a one-off; a surplus can be a gift that arrived early. Look at the trend."
        ),
    )]


def _reserves(f: Filing) -> list[Fact]:
    if f.net_assets is None or f.expenses is None or f.expenses <= 0:
        return []
    if f.net_assets <= 0:
        return [Fact(
            key="reserves",
            label="Reserves",
            display="none on paper",
            meaning=(
                f"At year end it owed more than it owned: net assets of "
                f"{money_display(f.net_assets)}."
            ),
            spoken=(
                "At year end it owed more than it owned, with negative net assets of "
                f"{money_spoken(f.net_assets)}."
            ),
            limit=(
                "Negative net assets can come from a pension liability or a building loan "
                "and still sit inside a working organization. It is a question, not a verdict."
            ),
        )]
    months = f.net_assets / (f.expenses / 12)
    return [Fact(
        key="reserves",
        label="Reserves",
        display=months_display(months),
        meaning=(
            f"Net assets of {money_display(f.net_assets)} would cover "
            f"{months_display(months)} of spending at the current pace."
        ),
        spoken=(
            f"Its net assets of {money_spoken(f.net_assets)} would cover "
            f"{months_spoken(months)} of spending at the current pace."
        ),
        limit=(
            "Net assets include buildings, equipment and gifts restricted to a purpose, "
            "so the part it could actually spend is smaller. Treat this as an upper bound."
        ),
    )]


def _people(f: Filing) -> list[Fact]:
    parts = [p for p in (f.officer_comp, f.other_salaries, f.payroll_tax) if p is not None]
    if not parts or f.expenses is None or f.expenses <= 0:
        return []
    total = sum(parts)
    share = total / f.expenses
    officer = ""
    officer_s = ""
    if f.officer_comp is not None and f.officer_comp > 0:
        who = "went to officers, directors and key employees"
        officer = f", of which {money_display(f.officer_comp)} {who}"
        officer_s = f", of which {money_spoken(f.officer_comp)} {who}"
    return [Fact(
        key="people",
        label="Pay",
        display=f"{pct_display(share)} of spending",
        meaning=f"About {pct_display(share)} of spending went to pay people{officer}.",
        spoken=f"About {pct_spoken(share)} of spending went to pay people{officer_s}.",
        limit=(
            "Most charities deliver their work through people, so a high share is not "
            "a bad sign by itself. What matters is what those people do, and the filing "
            "does not say. Benefits and pensions are not in this feed, so the real "
            "people cost is a little higher."
        ),
    )]


def _sources(f: Filing) -> list[Fact]:
    if f.revenue is None or f.revenue <= 0:
        return []
    pieces: list[tuple[str, float]] = []
    for label, value in (
        ("donations and grants", f.contributions),
        ("fees for its services", f.program_revenue),
        ("investments", f.investment_income),
    ):
        share = _share(value, f.revenue)
        if share is not None and share >= 0.01:
            pieces.append((label, share))
    if not pieces:
        return []
    pieces.sort(key=lambda p: p[1], reverse=True)
    text = ", ".join(f"{pct_display(s)} from {label}" for label, s in pieces)
    text_s = ", ".join(f"{pct_spoken(s)} from {label}" for label, s in pieces)
    lead_label, lead_share = pieces[0]
    return [Fact(
        key="sources",
        label="Where the money comes from",
        display=f"{pct_display(lead_share)} {lead_label}",
        meaning=f"Revenue came {text}.",
        spoken=f"Its revenue came {text_s}.",
        limit=(
            "This is where the money comes from, not what it achieved. An organization "
            "that charges for services is not less charitable for it."
        ),
    )]


def _fundraising(f: Filing) -> list[Fact]:
    facts: list[Fact] = []
    if (
        f.pro_fundraising_fees is not None
        and f.pro_fundraising_fees > 0
        and f.contributions is not None
        and f.contributions > 0
    ):
        cents = f.pro_fundraising_fees / f.contributions * 100
        cents_text = "under 1 cent" if cents < 1 else f"about {round(cents)} cents"
        facts.append(Fact(
            key="fundraisers",
            label="Paid fundraisers",
            display=f"{cents_text} per dollar",
            meaning=(
                f"It paid {money_display(f.pro_fundraising_fees)} to professional "
                f"fundraisers, {cents_text} for every dollar donated."
            ),
            spoken=(
                f"It paid {money_spoken(f.pro_fundraising_fees)} to professional "
                f"fundraisers, {cents_text} for every dollar donated."
            ),
            limit=(
                "Only fees to outside fundraising firms show here. In-house fundraising "
                "staff sit inside the pay figure above."
            ),
        ))
    if (
        f.fundraising_gross is not None
        and f.fundraising_gross > 0
        and f.fundraising_direct_costs is not None   # no costs on file, no fact: never assume zero
    ):
        costs = f.fundraising_direct_costs
        net = f.fundraising_gross - costs
        kept = _share(net, f.fundraising_gross)
        if kept is not None and net < 0:
            facts.append(Fact(
                key="events",
                label="Fundraising events",
                display="cost more than they raised",
                meaning=(
                    f"Fundraising events brought in {money_display(f.fundraising_gross)} "
                    f"and cost {money_display(costs)} to run, so they lost money."
                ),
                spoken=(
                    f"Fundraising events brought in {money_spoken(f.fundraising_gross)} "
                    f"and cost {money_spoken(costs)} to run, so they lost money."
                ),
                limit=(
                    "Events often exist to build a community as much as to raise money, "
                    "so losing money on them is not automatically waste."
                ),
            ))
        elif kept is not None:
            facts.append(Fact(
                key="events",
                label="Fundraising events",
                display=f"kept {pct_display(kept)}",
                meaning=(
                    f"Fundraising events raised {money_display(f.fundraising_gross)} and "
                    f"kept {pct_display(kept)} after direct costs."
                ),
                spoken=(
                    f"Fundraising events raised {money_spoken(f.fundraising_gross)} and "
                    f"kept {pct_spoken(kept)} after direct costs."
                ),
                limit=(
                    "Events often exist to build a community as much as to raise money, "
                    "so a thin margin is not automatically waste."
                ),
            ))
    return facts


def _trend(years: tuple[YearRow, ...]) -> list[Fact]:
    rows = [y for y in years if y.revenue is not None and y.expenses is not None]
    if len(rows) < 3:
        return []
    first, last = rows[0], rows[-1]
    deficits = sum(1 for y in rows if y.deficit)
    if first.revenue and first.revenue > 0:
        change = (last.revenue or 0) / first.revenue
        if change >= 1.05:
            verb = "grew"
        elif change <= 0.95:
            verb = "shrank"
        else:
            verb = "held steady"
        movement = (
            f"Revenue {verb} from {money_display(first.revenue)} in {first.year} "
            f"to {money_display(last.revenue or 0)} in {last.year}"
        )
        movement_s = (
            f"Revenue {verb} from {money_spoken(first.revenue)} in {first.year} "
            f"to {money_spoken(last.revenue or 0)} in {last.year}"
        )
    else:
        movement = f"Revenue was {money_display(last.revenue or 0)} in {last.year}"
        movement_s = f"Revenue was {money_spoken(last.revenue or 0)} in {last.year}"
    deficit_text = (
        f"it spent more than it took in in {deficits} of the {len(rows)} years on file"
        if deficits
        else f"it took in more than it spent in every one of the {len(rows)} years on file"
    )
    return [Fact(
        key="trend",
        label="Trend",
        display=f"{len(rows)} years, {deficits} in deficit",
        meaning=f"{movement}, and {deficit_text}.",
        spoken=f"{movement_s}, and {deficit_text}.",
        limit=(
            "Filings arrive a year or more after the fiscal year ends. The newest year "
            "here may already be two years old."
        ),
    )]


def _caveats(org: Organization, latest: Filing | None, status: Status) -> tuple[str, ...]:
    out: list[str] = []
    if status == "no_financials":
        out.append(
            "No financial numbers are on file in this feed. Organizations under 50 thousand "
            "dollars a year file a short postcard with no figures, and churches do not have "
            "to file at all. Missing numbers are not a warning sign by themselves."
        )
    if status == "foundation":
        out.append(
            "This is a private foundation. It mostly gives grants rather than running "
            "programs, so its spending is largely grants paid out, and pay and donation "
            "ratios mean something different from a charity that runs services."
        )
    if latest is not None and latest.form == "990-EZ":
        out.append(
            "It files the short form, 990-EZ, used by organizations under 200 thousand "
            "dollars in receipts. It carries fewer line items, so a fact missing above "
            "may be unreported rather than zero."
        )
    out.append(
        "A tax filing does not measure impact. It cannot tell you whether the work is any "
        "good, only what it cost."
    )
    out.append(
        "The split between program, administration and fundraising spending is on the "
        "filing itself but not in this data feed, so it is not shown here."
    )
    if latest is not None:
        lag = f"The newest numbers here cover the fiscal year ending {latest.period_text}."
        if org.newer_filings_without_data:
            n = org.newer_filings_without_data
            lag += (
                f" ProPublica lists {n} more filing{'s' if n != 1 else ''} whose numbers are "
                "not in the feed yet."
            )
        out.append(lag + " Anything since then is not visible.")
    return tuple(out)
