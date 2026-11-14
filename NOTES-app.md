# NOTES-app

- `rewind/search.py`, `rewind/store.py` and `rewind/quiz.py` did not exist in this checkout; built `quiz.py` and used `tests/fakes.py` (`REWIND_FAKE=1`) for search/store. app.py uses the stub signatures from docs/agent-prompts.md.
- Streamlit 1.67 supports `st.video(end_time=...)` for YouTube, so the end time is enforced in the player.
- Course/lecture filter is applied after `search.answer` (it has no filter argument): the best + alternatives are filtered by lecture. If the match is only in another lecture, the app says so and does NOT flag a gap.
- Run: `$env:REWIND_FAKE="1"; streamlit run app.py`
