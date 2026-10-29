from altk_forge.trajectory import text


def test_readable():
    assert text.readable('{"answer": "plain"}') == "plain"
    assert text.readable('{"error": "bad"}') == "Error: bad"
    assert text.readable("## Title\n**bold**") == "Title\nbold"
    assert text.readable("<p>One</p><ul><li>a</li></ul>") == "One\n• a"
    assert text.readable(None) == ""


def test_clip():
    assert text.clip("abc", 5) == "abc"
    assert text.clip("abcdef", 3) == "abc… [3 more characters]"
    assert text.clip(None) == ""
