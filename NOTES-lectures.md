# NOTES-lectures

Real lecture data for the demo, built without an API key. I wrote each lecture's raw LLM answer by hand,
following SNIPPET_PROMPT strictly, then ran it through the normal post-processing.

Python: `..\cmo-venv\Scripts\python.exe`. `tests/test_snippet_maker.py`: **51 passed**.

## Lectures

| id | lecture | video | length | snippets |
|---|---|---|---|---|
| econ01 | MIT 14.01 Principles of Microeconomics (Fall 2018), Lecture 1 "Introduction and Supply & Demand", Jonathan Gruber | https://www.youtube.com/watch?v=_OkTw766oCs | 34.6 min | 19 (53–119 s), 27:50 covered |
| psych02 | Yale PSYC 110 Introduction to Psychology, Lecture 2 "Foundations: This Is Your Brain", Paul Bloom | https://www.youtube.com/watch?v=vg01Q1BI4WM | 53.3 min | 37 (47–121 s), 51:58 covered |

Each folder holds `meta.json`, `transcript.json` (YouTube captions, en), `raw_llm_output.json` (the hand-written
model answer) and `snippets.json`. Skipped as admin: econ01's intro on course details and teaching style
(0:00–3:25) and its closing course roadmap and logistics (31:40–end).
Rejected sources: Bloom Lecture 1 (mostly course overview) and Walter Lewin's 8.01 (MIT withdrew his materials).

## What I changed in rewind/snippet_maker.py

1. **`--export-prompt FILE`**: fetches or loads the transcript, saves transcript.json and meta.json, and writes
   the exact system prompt and user message to FILE (`FILE.partN.ext` per window if split). No API call.
   It shares `build_user_messages()` with the real API path, so the export is byte-identical to what the model gets.
2. **`--import-raw FILE`**: parses raw model JSON (fences or prose tolerated, same as `ask_json`) and runs the
   normal `finalize()`. Without `--youtube`/`--transcript` it uses the lecture's saved transcript.json.
3. **SNIPPET_PROMPT**, from reviewing the output:
   - **Sentence-aligned cuts.** YouTube caption lines often split sentences: Bloom's lines look like
     "…our immaterial minds. Now, Descartes made two". My first pass had 13 psych02 snippets starting
     mid-sentence (e.g. "arguments for dualism…", "neurons. There are a lot…"). The new rule is to start on the
     line where the topic's first sentence begins, even if it carries the previous sentence's last words, and to
     end after the line that completes the last sentence.
   - **Skip agendas and roadmaps.** Lists of topics for this or later lectures, and talk about teaching style,
     are now named explicitly as admin. Gruber's closing roadmap and his teaching-style intro were borderline
     under the old wording.
   - **Answer positions.** The rule was only "vary its position". My first pass put the right answer at B in 63
     of 74 psych02 questions and 21 of 38 econ01 questions, so a student could pass by always picking B, and
     models have the same bias. The new rule asks for an even spread over 0–3 that is not mostly one position.
4. **Post-processing:** `warn_answer_skew()` in `finalize()` logs a warning when one position holds more than
   50% of the answers (8 questions minimum). It has a test.

Fixes to my own raw JSON, not caused by the prompt rules:
- Two recall questions (axis labels on the rose graph; how kidneys are allocated today) became application questions.
- psych02-013 ran 135 s; I ended it at 19:06.
- I shuffled the options with a fixed seed. Answers are now econ01 A5/B12/C10/D11 and psych02 A18/B16/C27/D13.

Review checklist after the fixes. Every snippet:
- starts on a sentence start and is 45–121 s
- has a concept phrased as a student would search it
- has a 2-sentence summary using only the professor's content
- has 2 application questions with one clearly right answer

Remaining nits:
- Some ends still drop the last few words of a sentence when the next topic starts on the same caption line.
  This is inherent in caption-level timestamps.
- psych02-012 is 121 s, 1 s over the soft limit.

## Demo questions (6 per lecture) and search check

Run without a key, so this is similarity only (`search.search`, top 3). `*` marks an expected snippet.

| lecture | type | question | top 1 (score) | hit |
|---|---|---|---|---|
| econ01 | own words | Why is water so cheap when diamonds cost a fortune? | econ01-007 Water-diamond paradox (0.675) | @1 |
| econ01 | own words | What does it mean that every choice has a hidden cost? | econ01-002 Opportunity cost (0.517) | @1 |
| econ01 | own words | How do you find the price where buyers and sellers agree? | econ01-009 Supply curve and market equilibrium (0.398) | @1 |
| econ01 | own words | What's the difference between asking what happens and what should happen in economics? | econ01-002 (0.542); 017, 004 | **miss** (expected 010 or 011) |
| econ01 | not covered | What is price elasticity of demand? | econ01-008 Demand curve (0.455) | above threshold |
| econ01 | homework | Solve: demand is Q = 1000 - 100P and supply is Q = 200P. Find the equilibrium price and quantity. | econ01-009 (0.518) | @1 |
| psych02 | own words | Is the mind just the brain, or do we have a soul? | psych02-035 Hard problem (0.577); 034, 005 | **miss** (expected 010/001/002; 005, Descartes on soul vs. body, is arguably right) |
| psych02 | own words | How do neurons send messages to each other? | psych02-017 Synapses and neurotransmitters (0.698) | @1 |
| psych02 | own words | Why do drunk people lose their inhibitions? | psych02-019 Curare and alcohol (0.471) | @1 |
| psych02 | own words | What happens when someone can see fine but can't recognize faces? | psych02-030 Agnosia, prosopagnosia (0.576) | @1 |
| psych02 | not covered | How does long-term memory work? | psych02-018 (0.321) | below threshold |
| psych02 | homework | Exercise 3: explain how an agonist differs from an antagonist and give one drug example of each. | psych02-018 Agonists and antagonists (0.518) | @1 |

**hit@1 8/10, hit@3 8/10.** Both homework questions hit at rank 1, and both start with keywords ("Solve",
"Exercise 3") that `quiz.is_homework` catches without an LLM.

Scores: correct hits 0.398–0.698 (lowest: equilibrium, 0.398). Not covered: elasticity 0.455, memory 0.321.

**Suggested SIMILARITY_THRESHOLD: 0.38** (now 0.35). It rejects the memory question and keeps every correct
hit, but the margin is thin (0.398 is the lowest correct hit), and this is only 12 questions.
The elasticity question (0.455) can't be separated by similarity alone, because "demand" dominates the
embedding. That case needs the LLM check in `search.answer`. I didn't edit config.

Notes for search (T2):
- The two misses both involve abstract paraphrases: "what happens vs. what should happen" for
  positive/normative, and "mind vs. soul" for dualism.
- The embedding text is concept + summary + the full transcript. The long transcript may dilute the concept.
  Worth testing concept + summary only, or a higher weight on the concept.

## Regenerating both lectures with a real key

Set in `.env`: `LLM_PROVIDER=anthropic`, `LLM_API_KEY=...`, `LLM_MODEL=<model id>`
(or the legacy `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`). Then:

```
..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --youtube https://www.youtube.com/watch?v=_OkTw766oCs --lecture-id econ01 --title "Introduction and Supply & Demand" --course "MIT 14.01 Principles of Microeconomics" --lecture-number 1 --languages en
..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --youtube https://www.youtube.com/watch?v=vg01Q1BI4WM --lecture-id psych02 --title "Foundations: This Is Your Brain" --course "Yale PSYC 110 Introduction to Psychology" --lecture-number 2 --languages en
..\cmo-venv\Scripts\python.exe -c "from rewind import search; search.index_all()"
```

These overwrite snippets.json. To compare against the hand-written version first, back up the folder.
Re-importing the hand-written answer without a key:
`..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --lecture-id econ01 --import-raw data/lectures/econ01/raw_llm_output.json`.
After a real run, check that the log has no "Correct answer is option X in N of M questions" warning.

Export the prompt only (no key needed): add `--export-prompt prompt.txt` to either command above.
