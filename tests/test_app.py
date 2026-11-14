import os
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest


def _run():
    os.environ["REWIND_FAKE"] = "1"
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()
    assert not at.exception
    return at


def test_found_then_wrong_answer_shows_alternative():
    with mock.patch("rewind.llm.ask_json", return_value={"homework": False}):
        at = _run()
        at.sidebar.text_input[0].set_value("Giulia").run()
        at.text_input[0].set_value("what is the row picture of one equation at a time")
        at.button[0].click().run()  # form submit
        assert not at.exception
        assert any("Check yourself" in h.value for h in at.header)
        at.radio[0].set_value(0).run()  # option 0 is wrong for question 1 of demo01-001
        [b for b in at.button if b.label == "Submit answer"][0].click().run()
        assert not at.exception
        assert any("Not quite" in e.value for e in at.error)
        assert any("explained another way" in s.value for s in at.subheader)


def test_not_covered():
    with mock.patch("rewind.llm.ask_json", return_value={"homework": False}):
        at = _run()
        at.sidebar.text_input[0].set_value("Giulia").run()
        at.text_input[0].set_value("zzzz qqqq")
        at.button[0].click().run()
        assert any("isn't covered" in i.value for i in at.info)
