from __future__ import annotations

from dataclasses import replace

from before_you_give.propublica import Organization
from before_you_give.reading import read


def fact(reading, key):
    return next(f for f in reading.facts if f.key == key)


def keys(reading):
    return [f.key for f in reading.facts]


def test_red_cross_reading_matches_the_filing_arithmetic(red_cross):
    r = read(red_cross)
    assert r.status == "ok"
    assert keys(r) == [
        "size", "balance", "reserves", "people", "sources", "fundraisers", "events", "trend",
    ]
    assert fact(r, "size").display == "$3.2 billion in, $3 billion out"
    balance = fact(r, "balance")
    assert balance.display == "$246 million surplus"
    assert "about 8% of what it spent" in balance.meaning
    reserves = fact(r, "reserves")
    assert reserves.display == "about 12 months"      # 3.02B / (2.97B / 12)
    assert "upper bound" in reserves.limit
    people = fact(r, "people")
    assert people.display == "43% of spending"        # (5.9M + 1.18B + 92M) / 2.97B
    assert "$5.9 million went to officers" in people.meaning
    sources = fact(r, "sources")
    assert sources.meaning.startswith("Revenue came 67% from fees for its services")
    assert "29% from donations and grants" in sources.meaning
    events = fact(r, "events")
    assert events.display == "cost more than they raised"
    assert events.meaning.endswith("so they lost money.")
    trend = fact(r, "trend")
    assert trend.display == "13 years, 6 in deficit"
    assert trend.meaning.startswith(
        "Revenue shrank from $3.5 billion in 2011 to $3.2 billion in 2023"
    )


def test_every_fact_carries_a_limit_and_a_spoken_form(red_cross, msf, pantry):
    for org in (red_cross, msf, pantry):
        for f in read(org).facts:
            assert f.limit and f.spoken and f.meaning and f.display
            assert "$" not in f.spoken and "%" not in f.spoken


def test_deficit_year_is_worded_as_spending_more(red_cross):
    latest = red_cross.latest
    flipped = replace(latest, revenue=latest.expenses - 100_000_000)
    org = replace(red_cross, filings=(flipped,) + red_cross.filings[1:])
    balance = fact(read(org), "balance")
    assert balance.display == "$100 million deficit"
    assert balance.meaning.startswith("It spent $100 million more than it took in")


def test_negative_net_assets_is_a_question_not_a_verdict(pantry):
    latest = pantry.latest
    org = replace(pantry, filings=(replace(latest, net_assets=-50_000),) + pantry.filings[1:])
    reserves = fact(read(org), "reserves")
    assert reserves.display == "none on paper"
    assert "not a verdict" in reserves.limit


def test_private_foundation_gets_the_foundation_caveat_and_no_pay_fact(foundation):
    r = read(foundation)
    assert r.status == "foundation"
    assert "people" not in keys(r) and "sources" not in keys(r) and "reserves" not in keys(r)
    assert "size" in keys(r) and "balance" in keys(r)
    assert any("private foundation" in c for c in r.caveats)


def test_no_filings_has_no_facts_but_still_has_caveats(empty):
    r = read(empty)
    assert r.status == "no_financials"
    assert r.facts == ()
    assert any("postcard" in c for c in r.caveats)
    assert any("does not measure impact" in c for c in r.caveats)


def test_caveats_always_name_the_newest_year_and_the_feed_gap(red_cross):
    r = read(red_cross)
    lag = [c for c in r.caveats if "fiscal year ending June 2023" in c]
    assert lag and "11 more filings" in lag[0]
    assert any("program, administration and fundraising" in c for c in r.caveats)


def test_all_none_filing_produces_a_reading_without_crashing(red_cross):
    latest = red_cross.latest
    blank = replace(
        latest, revenue=None, expenses=None, assets=None, liabilities=None, net_assets=None,
        officer_comp=None, other_salaries=None, payroll_tax=None, contributions=None,
        program_revenue=None, investment_income=None, pro_fundraising_fees=None,
        fundraising_gross=None, fundraising_direct_costs=None,
    )
    org: Organization = replace(red_cross, filings=(blank,))
    r = read(org)
    assert r.facts == () and r.status == "ok" and len(r.caveats) >= 3


def test_year_rows_are_oldest_first_with_deficit_flags(red_cross):
    years = read(red_cross).years
    assert years[0].year == 2011 and years[-1].year == 2023
    assert years[-1].deficit is False
    assert sum(1 for y in years if y.deficit) == 6
