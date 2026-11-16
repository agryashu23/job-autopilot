"""Thin client for a local Ollama instance. No tokens, no cloud.

All calls degrade gracefully: if Ollama isn't installed/running or the model
isn't pulled yet, callers get None and fall back to heuristics.
"""
import json
import re

import requests

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:3b"   # small, fast, good at structured extraction


def available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return r.ok
    except Exception:
        return False


def model_ready() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        names = [m["name"] for m in r.json().get("models", [])]
        return any(n.startswith(MODEL.split(":")[0]) for n in names)
    except Exception:
        return False


def generate(prompt: str, timeout: int = 180) -> str | None:
    """Single-shot completion. Returns None on any failure."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_ctx": 8192,
                              "num_predict": 1500}},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception:
        return None


def generate_json(prompt: str, timeout: int = 180):
    """Completion that must return JSON; extracts the first {...} block."""
    text = generate(prompt, timeout)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # common LLM slip: trailing commas
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except json.JSONDecodeError:
            return None
