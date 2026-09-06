from __future__ import annotations

from before_you_give.narration import SOURCE_LINE, script, word_count
from before_you_give.reading import read


def test_script_is_deterministic_and_sourced(red_cross):
    a = script(read(red_cross))
    b = script(read(red_cross))
    assert a == b
    assert a.startswith(
        "Here is what the public tax filings say about American National Red Cross, "
        "in Washington, DC."
    )
    assert "Now, what these filings cannot tell you." in a
    assert a.endswith(SOURCE_LINE)


def test_script_reads_aloud_without_signs(red_cross, msf, pantry, foundation, empty):
    for org in (red_cross, msf, pantry, foundation, empty):
        text = script(read(org))
        assert "$" not in text and "%" not in text and "K " not in text
        assert 60 <= word_count(text) <= 320, (org.name, word_count(text))


def test_script_for_no_financials_still_explains(empty):
    text = script(read(empty))
    assert "No financial numbers are on file" in text
    assert "Now, what these filings cannot tell you." not in text
