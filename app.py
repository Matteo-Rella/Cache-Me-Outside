"""Rewind: student view (Streamlit, page 1 of the multipage app; pages/lecturer.py is page 2).

Flow: ask a question -> search.answer -> clip (video, timestamps, summary) -> "Check yourself" quiz.
Everything the student sees lives in st.session_state["current"] so clicks never reset the quiz.
Set REWIND_FAKE=1 to use tests/fakes.py instead of rewind.search and rewind.store.
"""
from __future__ import annotations

import os

import streamlit as st

from rewind import lectures, quiz

if os.environ.get("REWIND_FAKE") == "1":
    from tests.fakes import search, store
else:
    from rewind import search, store

NOT_COVERED_MSG = "This isn't covered in the recorded lectures. I've sent your question to the lecturer."
HOMEWORK_MSG = "Here's where the prof explains the method. Try it yourself, then check with the questions below."

st.set_page_config(page_title="Rewind", page_icon="⏪", layout="centered")


# ---------- helpers ----------

def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@st.cache_data(show_spinner=False)
def _lecture_metas() -> dict[str, dict]:
    return {lid: lectures.load_meta(lid) for lid in lectures.list_lectures()}


def render_clip(snippet: dict, heading: str | None = None) -> None:
    meta = _lecture_metas().get(snippet["lecture_id"]) or lectures.load_meta(snippet["lecture_id"])
    if heading:
        st.subheader(heading)
    st.markdown(f"### {snippet['concept']}")
    st.caption(
        f"Lecture {meta.get('lecture_number', '')}: {meta['title']} · "
        f"{fmt_time(snippet['start'])}–{fmt_time(snippet['end'])}"
    )
    st.write(snippet["summary"])
    st.video(meta["video_url"], start_time=int(snippet["start"]), end_time=int(snippet["end"]))


def new_current(question: str, result: dict, snippet: dict | None, homework: bool = False) -> dict:
    alts = [s for s in (result.get("alternatives") or []) if snippet is None or s["id"] != snippet["id"]]
    return {
        "question": question,
        "status": "found" if snippet else result["status"],
        "snippet": snippet,
        "alternatives": alts,
        "homework": homework,
        "answers": {},  # question_index -> {"chosen", "correct", "explanation", "alt"}
        "outside_filter": False,
    }


# ---------- state ----------

st.session_state.setdefault("current", None)
st.session_state.setdefault("student_name", "")
try:
    store.init_db()
except Exception:
    pass


def open_snippet(snippet_id: str) -> None:
    snippet = lectures.get_snippet(snippet_id)
    if snippet:
        st.session_state.current = new_current(
            "Rewatch", {"status": "found", "alternatives": []}, snippet
        )


def submit_answer(snippet: dict, index: int, student_id: str) -> None:
    chosen = st.session_state.get(f"choice-{snippet['id']}-{index}")
    if chosen is None:
        return
    cur = st.session_state.current
    result = quiz.grade(snippet, index, chosen)
    store.log_attempt(student_id, snippet["id"], index, chosen, result["correct"])
    alt = None
    if not result["correct"]:
        used = sum(1 for a in cur["answers"].values() if a["alt"])
        if used < len(cur["alternatives"]):
            alt = cur["alternatives"][used]
    cur["answers"][index] = {"chosen": chosen, **result, "alt": alt}


# ---------- sidebar ----------

with st.sidebar:
    st.header("You")
    st.text_input("Your name", key="student_name", placeholder="e.g. Giulia")
    student_id = st.session_state.student_name.strip()

    metas = _lecture_metas()
    courses = sorted({m["course"] for m in metas.values()})
    course = st.selectbox("Course", ["All"] + courses)
    in_course = {lid: m for lid, m in metas.items() if course == "All" or m["course"] == course}
    lecture_choice = st.selectbox(
        "Lecture",
        ["All"] + list(in_course),
        format_func=lambda lid: "All lectures" if lid == "All" else
        f"{in_course[lid].get('lecture_number', '')}. {in_course[lid]['title']}",
    )
    allowed = set(in_course) if lecture_choice == "All" else {lecture_choice}

    st.header("Rewatch before the exam")
    if not student_id:
        st.caption("Enter your name to see your weak spots.")
    else:
        weak = store.student_weak_spots(student_id)
        if not weak:
            st.caption("Nothing yet. Questions you get wrong will show up here.")
        for sid in weak:
            snip = lectures.get_snippet(sid)
            if snip:
                st.button(
                    f"{snip['concept']} ({fmt_time(snip['start'])})",
                    key=f"weak-{sid}",
                    on_click=open_snippet,
                    args=(sid,),
                    use_container_width=True,
                )

# ---------- main ----------

st.title("Rewind")
st.caption("Ask a question and watch the exact moment your professor explains it.")

with st.form("ask"):
    question = st.text_input("Ask about the lecture", placeholder="e.g. What is the row picture?")
    asked = st.form_submit_button("Ask")

if asked and question.strip():
    if not student_id:
        st.warning("Please enter your name in the sidebar first.")
    else:
        question = question.strip()
        with st.spinner("Finding the moment..."):
            result = search.answer(question)
            store.log_ask(student_id, question, result)
            homework = quiz.is_homework(question)
        if result["status"] == "found":
            candidates = [result["best"]] + list(result.get("alternatives") or [])
            in_scope = [s for s in candidates if s["lecture_id"] in allowed]
            if in_scope:
                rest = dict(result, alternatives=in_scope[1:])
                st.session_state.current = new_current(question, rest, in_scope[0], homework)
            else:
                cur = new_current(question, result, None, homework)
                cur["status"] = "outside_filter"
                st.session_state.current = cur
        else:
            store.flag_gap(student_id, question)
            st.session_state.current = new_current(question, result, None, homework)

cur = st.session_state.current
if cur:
    snippet = cur["snippet"]
    if cur["status"] == "not_covered":
        st.info(NOT_COVERED_MSG)
    elif cur["status"] == "outside_filter":
        st.info("I found this in another lecture, but not in the one you selected. Choose \"All lectures\" to see it.")
    elif snippet:
        if cur["homework"]:
            st.info(HOMEWORK_MSG)
        render_clip(snippet)

        st.divider()
        st.header("Check yourself")
        questions = snippet["questions"]
        # Show every answered question, plus the next unanswered one.
        for i, q in enumerate(questions):
            answered = cur["answers"].get(i)
            if answered is None and i > len(cur["answers"]):
                break
            st.markdown(f"**Question {i + 1}.** {q['q']}")
            if answered:
                st.radio(
                    "Your answer", q["options"], index=answered["chosen"], disabled=True,
                    key=f"done-{snippet['id']}-{i}", label_visibility="collapsed",
                )
                if answered["correct"]:
                    st.success(f"Well done! {answered['explanation']}")
                else:
                    st.error(f"Not quite. {answered['explanation']}")
                    if answered["alt"]:
                        render_clip(answered["alt"], heading="Watch it explained another way")
            else:
                st.radio(
                    "Your answer", range(len(q["options"])), index=None,
                    format_func=lambda n, opts=q["options"]: opts[n],
                    key=f"choice-{snippet['id']}-{i}", label_visibility="collapsed",
                )
                st.button(
                    "Submit answer", key=f"submit-{snippet['id']}-{i}",
                    on_click=submit_answer, args=(snippet, i, student_id or "anonymous"),
                )
                break
        else:
            st.caption("You've answered all the questions for this clip.")
