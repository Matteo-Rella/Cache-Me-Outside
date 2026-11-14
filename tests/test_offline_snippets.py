"""rewind.offline_snippets: moments and questions without an LLM keep the snippet contract."""
from rewind import offline_snippets


def _segments():
    topics = [
        "Friction slows the user down on purpose so the user stops and thinks about the filter bubble.",
        "Negotiation gives the user agency to disagree with the generated answer and tune the outcome.",
        "Participation makes the interaction collective so many people make sense of the model together.",
        "Diagnosis comes first in the research process and then the team reframes the design problem.",
        "A diary records the daily tasks and what people felt while they used the chatbot at home.",
    ]
    segs, t = [], 0.0
    for topic in topics:
        for _ in range(12):
            segs.append({"start": t, "end": t + 7.0, "text": topic})
            t += 7.0
    return segs


def test_moments_follow_the_contract():
    snippets = offline_snippets.make_snippets(_segments(), "off01")
    assert len(snippets) >= 4
    for s in snippets:
        assert s["id"].startswith("off01-") and s["lecture_id"] == "off01"
        assert s["concept"] and s["summary"] and s["text"]
        assert 40 <= s["end"] - s["start"] <= 150
        for q in s["questions"]:
            assert len(q["options"]) == 4 and len(set(q["options"])) == 4
            assert 0 <= q["answer"] < 4 and q["explanation"]
    assert sum(len(s["questions"]) for s in snippets) >= len(snippets)


def test_empty_transcript_gives_nothing():
    assert offline_snippets.make_raw([]) == []
