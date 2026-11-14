# Cache-Me_Outside

Team repo for **Rewind**: ask a question, get the exact 60–90 seconds of your professor's lecture that answers it, then a quick exercise to check you got it.

- Concept: [docs/concept.md](docs/concept.md)
- Rules and data formats for coding agents: [AGENTS.md](AGENTS.md)
- Prompts for each module: [docs/agent-prompts.md](docs/agent-prompts.md)

Code lives on the `rewind` branch.

## Setup (once code lands)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # add your Anthropic API key
streamlit run app.py
```
