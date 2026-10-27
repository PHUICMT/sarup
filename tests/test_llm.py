"""Model selection and setup hints for the optional Ollama backend."""

from sarup import llm
from sarup.llm import GEN_PREFS, pick_model, setup_hint


def test_pick_model_follows_preference_order():
    installed = ["qwen3:8b", "gemma3:12b", "gemma4:12b"]
    assert pick_model(installed, GEN_PREFS) == "gemma4:12b"


def test_pick_model_matches_untagged_preference():
    assert pick_model(["gemma3:27b"], ["gemma3"]) == "gemma3:27b"


def test_pick_model_none_when_nothing_fits():
    assert pick_model(["llama3:8b"], GEN_PREFS) is None


def _ollama(monkeypatch, installed, up=None):
    monkeypatch.delenv("SARUP_ABSTRACTIVE_MODEL", raising=False)
    monkeypatch.setattr(llm, "list_models", lambda *a, **k: installed)
    monkeypatch.setattr(llm, "ollama_available", lambda *a, **k: bool(installed) if up is None else up)


def test_setup_hint_without_ollama_says_install_and_pull(monkeypatch):
    _ollama(monkeypatch, [])
    hint = setup_hint("pipeline")
    assert "https://ollama.com" in hint
    assert "ollama pull nomic-embed-text" in hint
    assert f"ollama pull {GEN_PREFS[0]}" in hint


def test_setup_hint_names_only_the_missing_model(monkeypatch):
    _ollama(monkeypatch, ["nomic-embed-text:latest"])
    hint = setup_hint("pipeline")
    assert "ollama.com" not in hint
    assert "nomic-embed-text" not in hint
    assert f"ollama pull {GEN_PREFS[0]}" in hint


def test_setup_hint_silent_when_ready_or_not_needed(monkeypatch):
    _ollama(monkeypatch, ["nomic-embed-text:latest", "gemma4:12b"])
    assert setup_hint("pipeline") is None
    _ollama(monkeypatch, [])
    assert setup_hint("extractive") is None
    assert setup_hint("auto") is None
