# Rewind – concept

## In one line

Ask a question, get the exact 60–90 seconds of your professor's lecture that answers it, then a quick exercise to check you got it.

## Why it's stronger than a normal Q&A bot

- **The answer is the professor's own words, not AI's.** The AI acts as a librarian that finds the right moment. That kills the hallucination worry and answers "is it new or just faster": nobody can search inside 40 hours of recordings today.
- **It makes you think.** Flow is: clip first, then one quick exercise. Only if you fail does it explain more or show a second clip. It never solves homework.
- **It admits gaps.** If no lecture covers the question, it says so and flags the question to the lecturer. That's the required "action beyond replying" plus the boundary case.

## Lecturer view

Which snippets get asked about most and which exercises students fail. That shows the prof where the lecture was unclear. Without it we help only students; with it we cover teaching too.

## How it works

1. Lecture video → transcript with timestamps.
2. Cut into snippets by topic, label each one: concept, lecture number, start/end time.
3. Store in a database.
4. Student asks → agent finds the best snippet → returns the video link at that timestamp plus a 2-line summary → generates 1–2 check questions → scores them.
5. Memory: what this student asked and failed, so it can suggest what to rewatch before the exam.

## Scope for the demo

- One real lecture (30–60 min), done well, demoed live.
- Streamlit app, Whisper for transcripts, embeddings for search, embedded video player that jumps to the timestamp.
- Level 3: the labelled snippets are already a knowledge graph (lecture → snippet → concept). Draw it. Add a QR code on the last lecture slide.

## Questions the jury will ask

- **Courses without recordings?** Works on slides plus audio recorded by the prof too.
- **Privacy and ownership of recordings?** Stays inside the university, prof opts in.
- **"Students will stop coming to lectures."** It's for after class, and the lecturer view improves the live lecture.
