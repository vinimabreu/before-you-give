"""python -m before_you_give "american red cross"  |  --ein 53-0196605  |  --fixture file.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from before_you_give.app import reading_payload
from before_you_give.narration import script, word_count
from before_you_give.propublica import Client, JsonCache, parse_organization
from before_you_give.reading import read


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="before_you_give")
    p.add_argument("query", nargs="?", help="organization name to search")
    p.add_argument("--ein", help="read one organization by EIN")
    p.add_argument("--state", help="two letter state filter for search")
    p.add_argument("--fixture", help="read a saved ProPublica JSON file instead of the network")
    p.add_argument("--json", action="store_true", help="print the JSON payload")
    p.add_argument("--cache", default=".cache/propublica")
    a = p.parse_args(argv)

    if a.fixture:
        org = parse_organization(json.loads(Path(a.fixture).read_text(encoding="utf-8")))
    else:
        client = Client(cache=JsonCache(Path(a.cache)))
        if a.ein:
            org = client.organization(a.ein)
        elif a.query:
            hits = client.search(a.query, a.state)
            if not hits:
                print("no organizations found", file=sys.stderr)
                return 1
            if len(hits) > 1:
                print("Matches (pass --ein to pick one):")
                for h in hits[:10]:
                    print(f"  {h.ein_text}  {h.name}  ({h.city}, {h.state})")
                print()
            org = client.organization(hits[0].ein)
        else:
            p.print_help()
            return 2

    reading = read(org)
    if a.json:
        print(json.dumps(reading_payload(reading), indent=2))
        return 0

    print(f"{org.name}  ({org.city}, {org.state})  EIN {org.ein_text}")
    print("=" * 72)
    if reading.latest:
        latest = reading.latest
        print(f"Latest filing: Form {latest.form}, fiscal year ending {latest.period_text}")
    print()
    print("WHAT THE FILINGS SHOW")
    for f in reading.facts:
        print(f"  {f.label:<26} {f.display}")
        print(f"      {f.meaning}")
        print(f"      limit: {f.limit}")
    print()
    print("WHAT THEY CANNOT TELL YOU")
    for c in reading.caveats:
        print(f"  - {c}")
    text = script(reading)
    print()
    print(f"NARRATION ({word_count(text)} words)")
    print(f"  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
