"""
Tests for the LLM layer's provider selection / auto-fallback ordering.
No network calls — only the chain logic and the deterministic template.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import app.llm as llm
from app.decision_engine import ProductInput, evaluate


def _set_env(monkeypatch, **kw):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_chain_none_is_template_only(monkeypatch):
    _set_env(monkeypatch, LLM_PROVIDER="none")
    assert llm._provider_chain() == []


def test_chain_primary_only_when_other_key_missing(monkeypatch):
    _set_env(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="x")
    assert llm._provider_chain() == ["openai"]


def test_chain_falls_back_to_other_when_key_present(monkeypatch):
    _set_env(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="x", ANTHROPIC_API_KEY="y")
    assert llm._provider_chain() == ["openai", "anthropic"]


def test_chain_anthropic_primary(monkeypatch):
    _set_env(monkeypatch, LLM_PROVIDER="anthropic", OPENAI_API_KEY="x", ANTHROPIC_API_KEY="y")
    assert llm._provider_chain() == ["anthropic", "openai"]


def test_image_plan_fallback_when_no_provider(monkeypatch):
    _set_env(monkeypatch, LLM_PROVIDER="none")
    plan = llm.draft_image_plan("Silicone molds")
    assert "Main:" in plan and "overlay text" in plan


def test_concept_images_empty_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.generate_concept_images("Silicone molds") == []


def test_explain_uses_fallback_template_when_all_providers_fail(monkeypatch):
    # primary openai but the call raises -> with no other key, lands on template
    _set_env(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="bad")
    monkeypatch.setitem(llm._CALLERS, "openai", lambda s, u, m: (_ for _ in ()).throw(RuntimeError("boom")))
    e = evaluate(ProductInput(name="x", price=27, cogs=6.5, fba_fee=3.30, monthly_sales=600))
    out = llm.explain(e)
    assert out.startswith("BUY") and "/100" in out
