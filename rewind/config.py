"""Configuration: paths, thresholds and environment loading.

Inputs: optional .env with LLM_PROVIDER, LLM_API_KEY and LLM_MODEL.
Outputs: paths, tuning constants and LLM settings; missing credentials are allowed.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
LECTURES_DIR = DATA_DIR / "lectures"
DB_PATH = DATA_DIR / "rewind.db"

SIMILARITY_THRESHOLD = 0.35
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def get_provider() -> str:
    """Return the selected provider, defaulting to Anthropic for legacy .env files."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower() or "anthropic"
    if provider not in {"anthropic", "openai", "gemini"}:
        raise ValueError("LLM_PROVIDER must be anthropic, openai, or gemini.")
    return provider


def get_api_key() -> str:
    """Return the generic key, or the legacy key when using Anthropic only."""
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key and get_provider() == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key


def get_model() -> str:
    """Return the configured model id, or an empty string when unconfigured."""
    model = os.environ.get("LLM_MODEL", "").strip()
    if not model and get_provider() == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    return model
