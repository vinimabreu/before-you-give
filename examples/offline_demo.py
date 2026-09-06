"""Offline walk through the five saved filings. No network, no key.

    python -m examples.offline_demo
"""
from __future__ import annotations

import json
from pathlib import Path

from before_you_give.narration import script, word_count
from before_you_give.propublica import parse_organization
from before_you_give.reading import read

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name.startswith("search_"):
            continue
        org = parse_organization(json.loads(path.read_text(encoding="utf-8")))
        reading = read(org)
        print(f"\n{'=' * 78}")
        print(f"{org.name}  ({org.city}, {org.state})  EIN {org.ein_text}  status={reading.status}")
        for f in reading.facts:
            print(f"  {f.label:<26} {f.display}")
        text = script(reading)
        print(f"\n  narration, {word_count(text)} words:\n  {text}")


if __name__ == "__main__":
    main()
