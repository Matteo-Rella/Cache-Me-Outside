"""Review snippet cuts by eye.

Run:    streamlit run tools/review_snippets.py
Input:  data/lectures/<lecture_id>/{meta,snippets}.json
Output: data/lectures/<lecture_id>/review_notes.json
        {"<snippet_id>": {"status": "good|bad cut|bad questions|unreviewed", "note": str,
                          "concept": str, "start": float, "end": float, "updated_at": iso str}}
Pick a lecture, pick a snippet, watch it from its start time, write a note.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from rewind import lectures  # noqa: E402
from rewind.snippet_maker import MAX_SECONDS, MIN_SECONDS  # noqa: E402

STATUSES = ["unreviewed", "good", "bad cut", "bad questions"]


def fmt(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def notes_path(lecture_id: str) -> Path:
    return lectures.lecture_dir(lecture_id) / "review_notes.json"


def load_notes(lecture_id: str) -> dict:
    path = notes_path(lecture_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_notes(lecture_id: str, notes: dict) -> None:
    notes_path(lecture_id).write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


st.set_page_config(page_title="Review snippets", layout="wide")
st.title("Review snippets")

lecture_ids = lectures.list_lectures()
if not lecture_ids:
    st.info("No lectures in data/lectures yet. Run `python -m rewind.snippet_maker` first.")
    st.stop()

lecture_id = st.sidebar.selectbox("Lecture", lecture_ids)
meta = lectures.load_meta(lecture_id)
snippets = lectures.load_snippets(lecture_id)
notes = load_notes(lecture_id)
if not snippets:
    st.warning(f"{lecture_id} has no snippets.json.")
    st.stop()

st.sidebar.caption(meta.get("title", ""))
reviewed = sum(1 for s in snippets if notes.get(s["id"], {}).get("status", "unreviewed") != "unreviewed")
st.sidebar.progress(reviewed / len(snippets), text=f"{reviewed}/{len(snippets)} reviewed")

# Overview table
rows = []
for s in snippets:
    dur = s["end"] - s["start"]
    n = notes.get(s["id"], {})
    rows.append(
        {
            "id": s["id"],
            "concept": s["concept"],
            "time": f"{fmt(s['start'])}-{fmt(s['end'])}",
            "dur (s)": round(dur),
            "length": "too long" if dur > MAX_SECONDS else ("short" if dur < 45 else ""),
            "questions": len(s.get("questions", [])),
            "status": n.get("status", "unreviewed"),
            "note": n.get("note", ""),
        }
    )
with st.expander("All snippets", expanded=False):
    st.dataframe(rows, hide_index=True, width="stretch")

# Snippet picker with prev/next
ids = [s["id"] for s in snippets]
key = f"idx_{lecture_id}"
if key not in st.session_state:
    st.session_state[key] = 0
c1, c2, c3 = st.columns([1, 6, 1])
if c1.button("◀ Prev", width="stretch"):
    st.session_state[key] = max(0, st.session_state[key] - 1)
if c3.button("Next ▶", width="stretch"):
    st.session_state[key] = min(len(ids) - 1, st.session_state[key] + 1)
idx = c2.selectbox(
    "Snippet",
    range(len(ids)),
    index=st.session_state[key],
    format_func=lambda i: f"{ids[i]} · {snippets[i]['concept']} · {fmt(snippets[i]['start'])}",
    label_visibility="collapsed",
)
st.session_state[key] = idx
s = snippets[idx]
dur = s["end"] - s["start"]

left, right = st.columns([3, 2])
with left:
    st.subheader(s["concept"])
    st.caption(f"{s['id']} · {fmt(s['start'])}–{fmt(s['end'])} · {dur:.0f} s")
    if dur > MAX_SECONDS:
        st.warning(f"Longer than {MAX_SECONDS} s.")
    elif dur < MIN_SECONDS:
        st.warning(f"Shorter than {MIN_SECONDS} s.")
    st.video(meta["video_url"], start_time=int(s["start"]), end_time=int(s["end"]) + 1)
    st.markdown(f"**Summary.** {s['summary']}")
    with st.expander("Transcript of this snippet"):
        st.write(s["text"])

with right:
    st.markdown("**Questions**")
    for qi, q in enumerate(s.get("questions", []), 1):
        st.markdown(f"{qi}. {q['q']}")
        for oi, opt in enumerate(q["options"]):
            mark = "✅" if oi == q["answer"] else "▫️"
            st.markdown(f"&nbsp;&nbsp;{mark} {opt}")
        st.caption(q["explanation"])

    st.divider()
    current = notes.get(s["id"], {})
    with st.form(f"note_{s['id']}"):
        status = st.radio(
            "Status", STATUSES, index=STATUSES.index(current.get("status", "unreviewed")), horizontal=True
        )
        note = st.text_area("Note", value=current.get("note", ""), placeholder="e.g. starts 5 s too late")
        if st.form_submit_button("Save note"):
            notes[s["id"]] = {
                "status": status,
                "note": note.strip(),
                "concept": s["concept"],
                "start": s["start"],
                "end": s["end"],
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            save_notes(lecture_id, notes)
            st.rerun()
