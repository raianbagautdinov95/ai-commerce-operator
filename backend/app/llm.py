"""
LLM explanation layer — provider-agnostic with automatic fallback.

IMPORTANT ARCHITECTURE RULE:
  The LLM never computes money or the verdict. It only turns the numbers the
  decision engine already produced into a clear, human explanation. This keeps
  results deterministic and is the difference between a real product and a
  "GPT wrapper".

Configure via env vars (see .env.example):
  LLM_PROVIDER = none | openai | anthropic   (primary; none = template only)
  LLM_MODEL    = override model for the PRIMARY provider (optional)
  LLM_LANG     = en | ru                       (explanation language)

Resilience: if the primary provider errors (rate limit, network, bad key) and
the other provider's key is configured, it is tried automatically. If every
provider fails, a deterministic template is returned — explanations never hard-fail.
"""
from __future__ import annotations

import logging
import os

from dotenv import find_dotenv, load_dotenv

from .decision_engine import Evaluation

# Load .env once (searching up from the working dir, e.g. backend/ -> repo root).
load_dotenv(find_dotenv(usecwd=True))

log = logging.getLogger("aco.llm")

_LANG_INSTRUCTION = {
    "en": "Write the explanation in clear, plain English.",
    "ru": "Дай объяснение на русском языке — живо, понятно, без канцелярита.",
}

_SYSTEM_BASE = (
    "You are an experienced Amazon FBA operating director and honest advisor. "
    "You are given a product's already-computed economics, score, verdict and "
    "reason. Explain the decision to a seller in 3-5 sentences: why this verdict, "
    "what is good, what is risky, and the single most useful next step. "
    "Never invent numbers — use only what is provided. Be direct, no hype, no "
    "profit guarantees."
)

_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_DEFAULT_MODEL = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5"}


def _system() -> str:
    lang = os.getenv("LLM_LANG", "en").lower()
    return _SYSTEM_BASE + " " + _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])


def _facts(e: Evaluation) -> str:
    eco = e.economics
    roi = "n/a" if eco.roi == float("inf") else f"{eco.roi:.0%}"
    return (
        f"Product: {e.name}\n"
        f"Profit/unit: ${eco.profit_per_unit}\n"
        f"Margin: {eco.margin:.0%}\n"
        f"ROI: {roi}\n"
        f"Est. monthly profit: ${eco.monthly_profit}\n"
        f"Score: {e.score}/100\n"
        f"Verdict: {e.verdict.value}\n"
        f"Reason: {e.reason}\n"
        f"Pros: {'; '.join(e.pros) or 'none'}\n"
        f"Risks: {'; '.join(e.risks) or 'none'}\n"
    )


def _fallback(e: Evaluation) -> str:
    parts = [f"{e.verdict.value} ({e.score}/100). {e.reason}"]
    if e.pros:
        parts.append("Strengths: " + "; ".join(e.pros))
    if e.risks:
        parts.append("Watch out for: " + "; ".join(e.risks))
    parts.append("Next: verify the exact FBA fee in Amazon's calculator and order supplier samples before committing.")
    return " ".join(parts)


def _call_openai(system: str, user: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model=model or _DEFAULT_MODEL["openai"],
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def _call_anthropic(system: str, user: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=model or _DEFAULT_MODEL["anthropic"],
        max_tokens=500, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


_CALLERS = {"openai": _call_openai, "anthropic": _call_anthropic}


def _provider_chain() -> list[str]:
    """Primary provider first, then the other one if its key is set (auto-fallback)."""
    primary = os.getenv("LLM_PROVIDER", "none").lower()
    if primary not in _CALLERS:
        return []  # 'none' or unknown -> template only
    chain = [primary]
    chain += [p for p in _CALLERS if p != primary and os.getenv(_KEY_ENV[p])]
    return chain


def _complete(system: str, user: str) -> str | None:
    """Run the provider chain for one prompt. Returns None if every provider fails."""
    primary = os.getenv("LLM_PROVIDER", "none").lower()
    primary_model = os.getenv("LLM_MODEL", "")
    for provider in _provider_chain():
        # LLM_MODEL only applies to the primary provider; fallbacks use their default.
        model = primary_model if provider == primary else ""
        try:
            return _CALLERS[provider](system, user, model)
        except Exception:
            log.warning("LLM provider '%s' failed; trying next.", provider, exc_info=True)
    return None


def explain(e: Evaluation) -> str:
    """Explain an evaluation in natural language, falling back across providers then template."""
    return _complete(_system(), _facts(e)) or _fallback(e)


# --- PPC Analyzer explanations ---

_PPC_SYSTEM_BASE = (
    "You are a seasoned Amazon Ads (PPC) manager. You are given an already-computed "
    "campaign summary and per-keyword recommendations (negate / lower bid / raise bid / "
    "keep). Explain in 3-5 sentences what is going well, where money is being wasted, "
    "and the 2-3 highest-impact actions to take first. Never invent numbers — use only "
    "what is provided. Be direct, no hype."
)


def _ppc_system() -> str:
    lang = os.getenv("LLM_LANG", "en").lower()
    return _PPC_SYSTEM_BASE + " " + _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])


def _ppc_facts(summary: dict, findings: list[dict]) -> str:
    lines = [
        f"Campaign: {summary['campaign']}",
        f"Total spend: ${summary['total_spend']}",
        f"Total sales: ${summary['total_sales']}",
        f"Overall ACOS: {summary['overall_acos']}",
        f"Break-even ACOS: {summary['break_even_acos']}",
        f"Target ACOS: {summary['target_acos']}",
        f"Wasted spend: ${summary['wasted_spend']}",
        f"Potential savings: ${summary['potential_savings']}",
        "Top recommendations:",
    ]
    for f in findings[:8]:
        lines.append(
            f"  - {f['action']} '{f['keyword']}' ({f['severity']}): {f['reason']}"
        )
    return "\n".join(lines)


def _ppc_fallback(summary: dict, findings: list[dict]) -> str:
    parts = [
        f"Campaign '{summary['campaign']}': spend ${summary['total_spend']}, "
        f"sales ${summary['total_sales']}, ACOS {summary['overall_acos']}. "
        f"Wasted spend ${summary['wasted_spend']}; potential savings ${summary['potential_savings']}."
    ]
    top = [f for f in findings if f["severity"] == "critical"][:3]
    if top:
        parts.append("Act first on: " + "; ".join(f"{f['action']} '{f['keyword']}'" for f in top) + ".")
    return " ".join(parts)


def explain_ppc(summary: dict, findings: list[dict]) -> str:
    """Explain a PPC analysis, falling back across providers then template."""
    return _complete(_ppc_system(), _ppc_facts(summary, findings)) or _ppc_fallback(summary, findings)


# --- Inventory Planner explanations ---

_INV_SYSTEM_BASE = (
    "You are an Amazon FBA inventory planner. You are given an already-computed "
    "summary and per-SKU findings (reorder now / soon / healthy / overstock / no sales). "
    "In 3-5 sentences, explain what needs ordering first to avoid stockouts, where "
    "capital is tied up in overstock, and the most important action this week. "
    "Never invent numbers — use only what is provided. Be direct."
)


def _inv_system() -> str:
    lang = os.getenv("LLM_LANG", "en").lower()
    return _INV_SYSTEM_BASE + " " + _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])


def _inv_facts(summary: dict, findings: list[dict]) -> str:
    lines = [
        f"SKUs: {summary['total_skus']}",
        f"At risk (reorder now): {summary['skus_at_risk']}",
        f"Total stock value: ${summary['total_stock_value']}",
        f"Reorder cost (now+soon): ${summary['total_reorder_cost']}",
        "Per-SKU:",
    ]
    for f in findings[:10]:
        cover = "n/a" if f["days_of_cover"] is None else f"{f['days_of_cover']}d cover"
        lines.append(
            f"  - {f['status']} '{f['name']}' ({f['severity']}): {cover}, "
            f"order {f['suggested_order_qty']} units. {f['reason']}"
        )
    return "\n".join(lines)


def _inv_fallback(summary: dict, findings: list[dict]) -> str:
    parts = [
        f"{summary['total_skus']} SKUs, {summary['skus_at_risk']} need reordering now. "
        f"Stock value ${summary['total_stock_value']}; reorder cost ${summary['total_reorder_cost']}."
    ]
    urgent = [f for f in findings if f["status"] == "REORDER_NOW"][:3]
    if urgent:
        parts.append("Order first: " + "; ".join(f"'{f['name']}' ({f['suggested_order_qty']} units)" for f in urgent) + ".")
    return " ".join(parts)


def explain_inventory(summary: dict, findings: list[dict]) -> str:
    """Explain an inventory analysis, falling back across providers then template."""
    return _complete(_inv_system(), _inv_facts(summary, findings)) or _inv_fallback(summary, findings)


# --- Daily Report briefing ---

_REPORT_SYSTEM_BASE = (
    "You are the seller's AI chief operating officer. You are given a prioritized, "
    "already-ranked list of today's issues across product research, ads and inventory, "
    "plus the money at stake. Write a short morning briefing (4-6 sentences): the single "
    "most urgent thing, then the next few, and the total money to protect or recover. "
    "Never invent numbers — use only what is provided. Be calm, direct and concrete."
)


def _report_system() -> str:
    lang = os.getenv("LLM_LANG", "en").lower()
    return _REPORT_SYSTEM_BASE + " " + _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])


def _report_facts(report: dict) -> str:
    lines = [
        f"Money at stake: ${report['money_at_stake_usd']}",
        f"Wasted ad spend: ${report['wasted_spend_usd']}",
        f"Reorder cost (now+soon): ${report['reorder_cost_usd']}",
        f"Severity counts: {report['counts']}",
        "Prioritized items:",
    ]
    for i in report["items"]:
        lines.append(f"  - [{i['severity']}] {i['module']}: {i['title']} (${i['impact_usd']}) — {i['detail']}")
    return "\n".join(lines)


def _report_fallback(report: dict) -> str:
    if not report["items"]:
        return "Nothing urgent today — no analyses on record yet. Run the modules to populate the report."
    top = report["items"][0]
    return (f"Top priority: {top['title']}. "
            f"${report['money_at_stake_usd']} is at stake across {len(report['items'])} items "
            f"(${report['wasted_spend_usd']} wasted ad spend, ${report['reorder_cost_usd']} reorders).")


def explain_report(report: dict) -> str:
    """Write the daily briefing, falling back across providers then template."""
    return _complete(_report_system(), _report_facts(report)) or _report_fallback(report)


# --- Listing draft (always English — it's the Amazon listing copy) ---

_LISTING_SYSTEM = (
    "You are an Amazon listing copywriter. Write a compelling, policy-compliant Amazon "
    "listing IN ENGLISH for the given product. Output plain text with three sections: "
    "'Title:' (one line, <=200 chars, keyword-rich), 'Bullets:' (exactly 5 lines, each "
    "starting with '- ', benefit-driven), and 'Description:' (2-3 short paragraphs). "
    "Use only the information provided — do NOT invent specs, materials, certifications, "
    "dimensions or guarantees. This is a draft for the seller to review."
)


def _listing_fallback(name: str) -> str:
    return (
        f"Title: {name}\n\n"
        "Bullets:\n"
        "- Add a key benefit here\n- Add a key feature here\n- Add a use case here\n"
        "- Add what's in the box here\n- Add a quality/guarantee note here\n\n"
        "Description:\nWrite 2-3 short paragraphs describing the product, who it's for, "
        "and why to choose it. (LLM unavailable — fill in manually.)"
    )


def draft_listing(name: str, attrs: str = "") -> str:
    """Draft an English Amazon listing (title, 5 bullets, description) for a product."""
    user = f"Product: {name}\n{attrs}".strip()
    return _complete(_LISTING_SYSTEM, user) or _listing_fallback(name)


# --- Image plan + concept images (the "Creative" agent) ---

_IMAGE_PLAN_SYSTEM = (
    "You are an Amazon product-photography director. For the given product, produce a SHOT "
    "LIST IN ENGLISH for a high-converting listing — about 6 images, each on its own line as: "
    "'<Image name>: <what the photo shows> | overlay text: <short on-image caption>'. Cover: "
    "Main (product on pure white), Lifestyle (in use), Infographic (key features), "
    "Dimensions/scale, What's in the box, and Trust/comparison. Use only the info provided; "
    "do not invent specs. Keep each line concise."
)


def _image_plan_fallback(name: str) -> str:
    return (
        f"Main: {name} on a pure white background | overlay text: (none)\n"
        "Lifestyle: the product being used in a real setting | overlay text: a key benefit\n"
        "Infographic: close-up with feature callouts | overlay text: 3-4 features\n"
        "Dimensions: product with measurements / hand for scale | overlay text: sizes\n"
        "What's in the box: all included items laid out | overlay text: what's included\n"
        "Trust: warranty / quality / comparison | overlay text: why choose us"
    )


def draft_image_plan(name: str, attrs: str = "") -> str:
    """Draft a listing photo shot-list (images + on-image text) for a product."""
    user = f"Product: {name}\n{attrs}".strip()
    return _complete(_IMAGE_PLAN_SYSTEM, user) or _image_plan_fallback(name)


def generate_concept_images(name: str, n: int | None = None) -> list[str]:
    """Generate concept/mockup listing images via OpenAI (existing key). Best-effort -> data URLs.

    These are CONCEPTS to visualise the listing, not real photos of the user's product.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return []
    count = n if n is not None else int(os.getenv("LLM_IMAGE_COUNT", "2"))
    model = os.getenv("LLM_IMAGE_MODEL", "gpt-image-1")
    prompt = (f"Professional e-commerce product photo concept for an Amazon listing of: {name}. "
              "Clean studio lighting, pure white background, sharp focus, high detail, centered. "
              "Concept mockup for visualization.")
    images: list[str] = []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        for _ in range(max(1, count)):
            resp = client.images.generate(model=model, prompt=prompt, n=1, size="1024x1024")
            d = resp.data[0]
            b64 = getattr(d, "b64_json", None)
            if b64:
                images.append("data:image/png;base64," + b64)
            elif getattr(d, "url", None):
                images.append(d.url)
    except Exception:
        log.warning("Concept image generation failed.", exc_info=True)
    return images


# --- Operator payroll explanations ---

_PAYROLL_SYSTEM_BASE = (
    "You report to the owner of an online store on whether their AI operator earned "
    "its keep. You are given an already-computed payroll: settled impact (money proven "
    "over completed measurement windows), provisional impact (measured but not yet "
    "settled), what the operator cost, and the net. In 3-4 sentences, state plainly "
    "whether it paid for itself, what drove the number, and what is still unmeasured. "
    "NEVER invent, recompute or round a number — use only what is provided, and never "
    "count provisional impact as proven. If the net is negative, say so directly."
)


def _payroll_system() -> str:
    lang = os.getenv("LLM_LANG", "en").lower()
    if lang == "ru":
        return _PAYROLL_SYSTEM_BASE + " Отвечай на русском языке."
    return _PAYROLL_SYSTEM_BASE


def _payroll_facts(payroll: dict, period_days: int) -> str:
    currency = payroll.get("currency", "USD")
    return (
        f"Period: last {period_days} days\n"
        f"Settled impact (proven): {payroll.get('settled_impact')} {currency}\n"
        f"Provisional impact (not yet settled): {payroll.get('provisional_impact')} {currency}\n"
        f"Operator cost: {payroll.get('operator_cost')} {currency}\n"
        f"Net: {payroll.get('net')} {currency}\n"
        f"Paid for itself: {payroll.get('paid_for_itself')}\n"
        f"Actions awaiting measurement: {payroll.get('awaiting_measurement')}\n"
        f"Action counts by status: {payroll.get('counts')}"
    )


def _payroll_fallback(payroll: dict, period_days: int) -> str:
    currency = payroll.get("currency", "USD")
    settled = payroll.get("settled_impact", 0.0)
    cost = payroll.get("operator_cost", 0.0)
    net = payroll.get("net", 0.0)
    awaiting = payroll.get("awaiting_measurement", 0)
    verdict = ("paid for itself" if payroll.get("paid_for_itself")
               else "has not yet paid for itself")
    tail = (f" {awaiting} action(s) are applied but not yet measured."
            if awaiting else "")
    return (f"Over the last {period_days} days the operator {verdict}: "
            f"{settled} {currency} of proven impact against {cost} {currency} of cost, "
            f"a net of {net} {currency}.{tail}")


def explain_payroll(payroll: dict, period_days: int = 30) -> str:
    """Narrate a payroll that has already been computed. The numbers are not ours to make."""
    return (_complete(_payroll_system(), _payroll_facts(payroll, period_days))
            or _payroll_fallback(payroll, period_days))
