# Styling the Rewind website – guide for the design side

Hi! This is everything you need to restyle (or redesign) the Rewind website with Claude. You don't need to know Python or run the backend.

## What Rewind is (30 seconds)

A student asks a question about a lecture and gets the exact 1–2 minutes of the professor's recording that answers it, with a short summary, then a quick quiz to check they understood. If no lecture covers it, the question is sent to the lecturer. The lecturer gets a dashboard showing where students get stuck.

The key message the design should carry: **the answer is your professor's own words, not AI's.** The AI is a librarian that finds the right moment. Calm, trustworthy, academic but modern; not "flashy AI startup".

## The pages

| Page | File | Who sees it |
|---|---|---|
| Student page | `web/index.html` | Students, on laptop and phone (they arrive via a QR code at the end of a lecture) |
| Lecturer dashboard | `web/lecturer.html` | The professor |
| QR page | `web/qr.html` | Projected full-screen on the last lecture slide |
| AI settings, chat panel | coming in the next rounds | Lecturer / students |

States to design for on the student page: first-visit name popup, empty home with the question box and example chips, loading, answer (video clip + summary + "Your professor's own words" badge), homework note, "not covered" card, "Check yourself" quiz (right / wrong / "See it explained another way"), and the "Rewatch before the exam" side panel.

## How it's built (so you know what to tell Claude)

- Plain HTML, CSS and JavaScript. No React, no npm, no build step.
- **All styling is in one file: `web/css/style.css`.** At the top there are design variables (colours, radius, shadows, fonts): changing those restyles the whole site.
- Current look: one indigo accent (`--accent: #4f46e5`), Inter for text, Instrument Serif for headings.
- Fonts and libraries are stored in `web/vendor/` so it works offline. If you change fonts, download the .woff2 files into `web/vendor/fonts/` instead of linking Google Fonts.
- Phone styles are in `@media (max-width: 640px)` blocks in the same file.

## 1. Get the code

1. Install GitHub Desktop (or use git if you prefer) and clone the repo **Cache-Me-Outside**.
2. Switch to the branch **`rewind`** (that's where the code is; `main` only has docs).
3. Create your own branch from it: **`rewind/style`** (Branch → New branch, based on `rewind`). Work only on this branch.

## 2. Run it on your computer (no backend needed)

The site has a **mock mode** with built-in fake data, so you only need a simple file server. In a terminal inside the repo folder:

```
python -m http.server 5500 -d web
```

Then open **http://localhost:5500/?mock=1** (student) and **http://localhost:5500/lecturer.html?mock=1** (lecturer).
No Python? Use the VS Code extension "Live Server" on the `web` folder and add `?mock=1` to the URL.

How to see each state in mock mode:
- **Answer:** click one of the example questions.
- **Not covered:** ask something unrelated, e.g. "Who won the 2018 World Cup?"
- **Homework note:** start the question with "Solve" or "Calculate".
- **Wrong answer flow:** pick a wrong option in "Check yourself".
- **Lecturer page:** click "Load demo data" in the footer to fill it.
- **Reset everything:** "Load demo data" also resets the mock state; to see the name popup again, clear the site's local storage (DevTools → Application → Local storage).

## 3. Rules (so your work merges cleanly with the rest)

Other people (and coding agents) are still adding features to the same pages: an AI settings page and a student chat panel.

- **You own `web/css/style.css`, `web/favicon.svg` and `web/vendor/fonts/`.** Change anything you like there.
- **HTML and JS:** you can change text and add wrapper elements, but **don't rename or remove ids, classes or `data-` attributes that the JavaScript uses**, and don't edit the logic in `web/js/`. If you're unsure, ask Claude: "search web/js for this class before changing it".
- Agents will add new styles for new features **at the end of style.css**, using the existing variables. Keep using variables (not hard-coded colours) so new features pick up your design automatically.
- Check both sizes before committing: **1280×720** (projector) and **390×844** (phone). No sideways scrolling on phones; buttons at least 40 px tall.
- Commit small and often on `rewind/style`, push, and tell the team when it's ready to merge.

## 4. Prompt to give Claude

Open the repo folder in Claude Code (or upload the `web/` folder to a Claude chat) and start with this, then add your own direction:

```
Read AGENTS.md and docs/website-styling-guide.md. I'm restyling the Rewind website in web/.
Rules: I own web/css/style.css, web/favicon.svg and web/vendor/fonts/. Don't change JavaScript logic, and don't rename or remove any id, class or data- attribute used in web/js/ (search before changing one). Use CSS variables for all colours, sizes and fonts. Fonts must be local .woff2 files in web/vendor/fonts/, not Google Fonts links. Don't run git commands unless I ask.
Run the site with: python -m http.server 5500 -d web, and check http://localhost:5500/?mock=1 and /lecturer.html?mock=1 at 1280×720 and 390×844 (use Playwright screenshots if available).

My direction: [describe the look you want: mood, colours, references, what to change]
Start by proposing 2–3 design directions (palette, fonts, card style, one sentence each), wait for me to pick one, then apply it across every page and state listed in the guide.
```

Good things to ask for once a direction is picked:
- "Make the video clip and the 'Your professor's own words' badge the hero of the answer view."
- "Make the lecturer dashboard feel like a calm report, not a busy admin panel."
- "Design the QR page so it reads from the back of a lecture hall."
- "Give me a logo mark for Rewind as an SVG (simple, works at 32 px)."
