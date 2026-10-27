"""Model selection for the optional Ollama backend."""

from sarup.llm import GEN_PREFS, pick_model


def test_pick_model_follows_preference_order():
    installed = ["qwen3:8b", "gemma3:12b", "gemma4:12b"]
    assert pick_model(installed, GEN_PREFS) == "gemma4:12b"


def test_pick_model_matches_untagged_preference():
    assert pick_model(["gemma3:27b"], ["gemma3"]) == "gemma3:27b"


def test_pick_model_none_when_nothing_fits():
    assert pick_model(["llama3:8b"], GEN_PREFS) is None
