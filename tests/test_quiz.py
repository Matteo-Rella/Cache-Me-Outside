from unittest import mock

from rewind import quiz

SNIP = {"questions": [{"q": "?", "options": ["a", "b"], "answer": 1, "explanation": "because"}]}


def test_grade():
    assert quiz.grade(SNIP, 0, 1) == {"correct": True, "explanation": "because"}
    assert quiz.grade(SNIP, 0, 0)["correct"] is False


def test_homework_keywords_skip_llm():
    with mock.patch("rewind.llm.ask_json") as m:
        assert quiz.is_homework("Solve this system: x+y=2")
        assert quiz.is_homework("calcola il determinante")
        assert quiz.is_homework("1) find x\n2) find y")
        m.assert_not_called()


def test_homework_llm():
    with mock.patch("rewind.llm.ask_json", return_value={"homework": False}):
        assert not quiz.is_homework("What is the row picture?")
    with mock.patch("rewind.llm.ask_json", return_value={"homework": True}):
        assert quiz.is_homework("Find the rank of the matrix [[1,2],[3,4]]")
    with mock.patch("rewind.llm.ask_json", side_effect=RuntimeError):
        assert not quiz.is_homework("What is a matrix?")
