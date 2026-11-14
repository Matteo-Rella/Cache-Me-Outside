"""Cut a transcript into moments and write check questions without any LLM or API key.

Inputs: transcript segments [{start, end, text}] (seconds) and a lecture id.
Outputs: the same snippet list as the LLM path (it goes through snippet_maker.finalize), so
search, the quiz and the web page need no changes.

How it works, with the standard library only:
1. Cuts: the transcript is walked in order; each moment is 60 to 120 seconds long and ends
   where the wording changes most (lowest word overlap between the lines before and after).
2. Key terms: words and two-word phrases that are frequent in a moment and rare in the rest
   of the lecture (TF-IDF).
3. Concept name: the best key terms. Summary: the two sentences that carry most key terms.
4. Questions: one "complete the professor's sentence" question (the missing term against
   terms from other moments) and one "which of these is explained here" question.

This is weaker than the LLM path: names are key terms, not written titles, and the questions
test recall of terms. Use it when there is no key, then let a lecturer review the result.
"""
from __future__ import annotations

import math
import re
from collections import Counter

MIN_SECONDS, TARGET_SECONDS, MAX_SECONDS = 60.0, 90.0, 120.0

STOP = set("""
a about above after again against all also am an and any are aren as at be because been before being below between
both but by can cannot could did do does doing don down during each even few for from further get go going got had has
have having he her here hers him his how i if in into is it its just kind know let like lot make many may maybe me mean
more most much my no nor not now of off ok okay on once one only or other our out over own really right same say see
she should so some something sort such take than that the their them then there these they thing things think this
those through to too two under until up us use very want was we well were what when where which while who why will
with would yeah yes you your actually basically going gonna little look bit different also still first second maybe
example instance course lecture today because around always never every already another called means said says
il lo la i gli le un una di da in con su per tra fra che e è non si come più anche ma se o del della dei delle al alla
questo questa quello quella sono era essere ha hanno abbiamo cosa cose quindi poi perché molto
""".split())

_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{2,}")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> list[str]:
    return [w.lower().strip("'-") for w in _WORD.findall(text) if w.lower() not in STOP]


def _terms(text: str) -> list[str]:
    """Single words plus two-word phrases of content words that stand next to each other."""
    raw = [w.lower().strip("'-") for w in _WORD.findall(text)]
    out = [w for w in raw if w not in STOP]
    for a, b in zip(raw, raw[1:]):
        if a not in STOP and b not in STOP:
            out.append(f"{a} {b}")
    return out


def cut(segments: list[dict]) -> list[tuple[int, int]]:
    """(first, last) segment index of each moment."""
    n = len(segments)
    bags = [set(_words(s["text"])) for s in segments]

    def cohesion(i: int, k: int = 6) -> float:  # overlap of the k lines before gap i with the k after
        left = set().union(*bags[max(0, i - k):i]) if i > 0 else set()
        right = set().union(*bags[i:min(n, i + k)])
        return len(left & right) / math.sqrt(len(left) * len(right)) if left and right else 0.0

    moments, first = [], 0
    while first < n:
        t0 = segments[first]["start"]
        best, best_score, j = None, None, first + 1
        while j < n and segments[j - 1]["end"] - t0 <= MAX_SECONDS:
            length = segments[j - 1]["end"] - t0
            if length >= MIN_SECONDS:
                ends_sentence = segments[j - 1]["text"].rstrip().endswith((".", "?", "!"))
                score = cohesion(j) + abs(length - TARGET_SECONDS) / 400 - (0.08 if ends_sentence else 0)
                if best_score is None or score < best_score:
                    best, best_score = j, score
            j += 1
        if best is None:
            best = j if j <= n else n
        if n - best < 4:  # don't leave a tiny tail
            best = n
        moments.append((first, best - 1))
        first = best
    return moments


def make_raw(segments: list[dict]) -> list[dict]:
    """Raw snippets in the shape the snippet prompt asks an LLM for."""
    segments = [s for s in segments if str(s.get("text", "")).strip()]
    if not segments:
        return []
    moments = cut(segments)
    texts = [" ".join(segments[i]["text"].strip() for i in range(a, b + 1)) for a, b in moments]
    counts = [Counter(_terms(t)) for t in texts]
    df = Counter(term for c in counts for term in c)
    n = len(texts)

    def score(term: str, c: Counter) -> float:
        bonus = 1.6 if " " in term else 1.0
        return c[term] * math.log((n + 1) / (df[term] + 0.5)) * bonus

    keys: list[list[str]] = []
    for c in counts:
        ranked = sorted((t for t in c if c[t] >= 2 or " " in t), key=lambda t: score(t, c), reverse=True)
        picked: list[str] = []
        for t in ranked:
            if any(t in p or p in t for p in picked):
                continue
            picked.append(t)
            if len(picked) == 6:
                break
        keys.append(picked or [t for t, _ in c.most_common(3)])

    raw = []
    for idx, ((a, b), text, c, ks) in enumerate(zip(moments, texts, counts, keys)):
        if not ks:
            continue
        sentences = [s.strip() for s in _SENT.split(text) if len(s.split()) >= 6] or [text]
        top = set(ks[:4])

        def weight(s: str) -> float:
            return sum(1 for k in top if k in s.lower()) / math.sqrt(len(s.split()))

        best = sorted(sorted(sentences, key=weight, reverse=True)[:2], key=sentences.index)
        summary = " ".join(best)
        if len(summary) > 420:
            summary = summary[:417].rsplit(" ", 1)[0] + "..."
        concept = ks[0].capitalize() + (f" and {ks[1]}" if len(ks) > 1 else "")

        others = [k for j, kk in enumerate(keys) if j != idx for k in kk[:3]]
        distract = [k for k in dict.fromkeys(others) if k not in text.lower() and all(k not in x and x not in k for x in ks)]
        questions = []
        # 1. complete the sentence
        single = next((k for k in ks if " " not in k), ks[0])
        sent = next((s for s in sentences if re.search(rf"\b{re.escape(single)}\b", s, re.I) and len(s.split()) <= 40), None)
        d1 = [k for k in distract if " " not in k][:3]
        if sent and len(d1) == 3:
            blank = re.sub(rf"\b{re.escape(single)}\b", "_____", sent, count=1, flags=re.I)
            opts = d1[:]
            opts.insert(idx % 4, single)
            questions.append({"q": f"Which word completes the professor's sentence? \"{blank}\"", "options": opts,
                              "answer": idx % 4, "explanation": f"The professor says: \"{sent}\""})
        # 2. which of these is explained here
        phrase = next((k for k in ks if k != single), single)
        d2 = [k for k in distract if k not in d1][:3] or distract[:3]
        if len(d2) == 3 and phrase not in d2:
            opts = d2[:]
            pos = (idx + 2) % 4
            opts.insert(pos, phrase)
            if len(set(opts)) == 4:
                questions.append({"q": "Which of these does the professor talk about in this part of the lecture?",
                                  "options": opts, "answer": pos,
                                  "explanation": f"This part is about {phrase}. The professor says: \"{best[0]}\""})
        raw.append({"concept": concept, "start": segments[a]["start"], "end": segments[b]["end"],
                    "summary": summary, "questions": questions})
    return raw


def make_snippets(segments: list[dict], lecture_id: str) -> list[dict]:
    """Finished snippets, through the same finalize() step as the LLM path."""
    from rewind import snippet_maker as sm

    return sm.finalize(make_raw(segments), segments, lecture_id)
