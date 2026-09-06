"""The spoken script, assembled from facts. Deterministic, template based.

No language model writes these sentences. Every figure a listener hears was
formatted from a field in the filing by code that can be read in reading.py,
so the narration cannot drift from the data even a little.
"""
from __future__ import annotations

from before_you_give.reading import Reading

SOURCE_LINE = "Source: ProPublica Nonprofit Explorer, built on I R S filings."


def script(reading: Reading) -> str:
    org = reading.org
    where = f", in {org.city}, {org.state}" if org.city and org.state else ""
    parts: list[str] = [
        f"Here is what the public tax filings say about {org.name}{where}."
    ]
    if reading.facts:
        parts.extend(f.spoken for f in reading.facts)
        parts.append("Now, what these filings cannot tell you.")
    parts.extend(reading.caveats)
    if reading.latest is not None and reading.latest.pdf_url:
        parts.append("If you want to check the numbers yourself, the filing is linked on the page.")
    parts.append(SOURCE_LINE)
    return " ".join(p.strip() for p in parts if p.strip())


def word_count(text: str) -> int:
    return len(text.split())
