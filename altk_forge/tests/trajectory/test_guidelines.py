from altk_forge.trajectory import guidelines
from altk_forge.trajectory.guidelines import guideline_count


def test_guideline_count_stops_at_the_next_heading():
    text = "# Guidelines from memory\n- one\n* two\n# Other\n- not a guideline\n"
    assert guideline_count({"input": [{"content": text}]}) == 2
    assert guideline_count({"input": [{"content": "no guidelines"}]}) == 0


def test_guidelines_in():
    text = "Intro\n## Guidelines from memory\nUse these:\n- one\n  continued\n- two\n## Next\n- other"
    start, end, intro, items = guidelines.guidelines_in(text)
    assert intro == "Use these:"
    assert items == ["one continued", "two"]
    assert text[start:end].startswith("## Guidelines") and "Next" not in text[start:end]
    assert guidelines.guidelines_in("nothing here") is None
    assert guidelines.guidelines_in(None) is None
