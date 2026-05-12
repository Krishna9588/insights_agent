"""
agent5_copilot.py
=================
Agent 5 — Product Copilot (Interactive Q&A)

Reads:  database_mock/{project_name}/db_document.json  (all agents must have run)
Writes: Nothing — read-only. Conversation history is kept in memory per session.

Callable from other scripts:
    from agent5_copilot import run_agent5
    run_agent5("Groww", provider="gemini")

Or as a single-shot query:
    from agent5_copilot import ask_copilot
    answer = ask_copilot("Groww", "What is the biggest trust problem?", provider="gemini")

What the Copilot knows:
    - Full company profile (Phase A + Phase B from Agent 1)
    - All validated problems with evidence (Agent 2)
    - Strategic insights with root causes, competitor gaps, opportunity sizes (Agent 3)
    - Product briefs with user flows, success metrics, risks (Agent 4)
    - Raw review signals (Play Store, App Store, Reddit, YouTube) if available

The Copilot does NOT answer from general knowledge — every answer
must be grounded in the actual db_document data for this project.
"""

import os
import json
import logging
from typing import Optional

from model_connect import call_llm

log = logging.getLogger("agent5")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DB_FOLDER = "database_mock"


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_db_document(project_name: str) -> dict:
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No db_document found for '{project_name}'. "
            f"Run Agent 1 (and ideally Agents 2-4) first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# KNOWLEDGE BASE BUILDER
# Assembles the complete research knowledge base from db_document.
# Structured so the LLM can answer ANY question about the company
# with evidence — not from general training knowledge.
# ─────────────────────────────────────────────────────────────────────────────

def _build_knowledge_base(db_doc: dict) -> dict:
    """
    Build a structured, token-efficient knowledge base from the full db_document.
    This is what the Copilot's system context is built from.
    """
    kb = {}
    processing_status = db_doc.get("processing_status", {})

    # ── Company Profile ───────────────────────────────────────────────────────
    cp      = db_doc.get("data_sources", {}).get("company_profile", {})
    profile = cp.get("data") or cp

    if isinstance(profile, dict) and not profile.get("error"):
        kb["company_profile"] = {
            "company_name"            : profile.get("company_name"),
            "domain"                  : profile.get("domain"),
            "industry_and_segment"    : profile.get("industry_and_segment"),
            "key_positioning"         : profile.get("key_positioning"),
            "revenue_model"           : profile.get("revenue_model"),
            "pricing_tiers"           : profile.get("pricing_tiers", []),
            "target_customer_segments": profile.get("target_customer_segments", []),
            "available_platforms"     : profile.get("available_platforms"),
            "no_of_users"             : profile.get("no_of_users"),
            "annual_revenue"          : profile.get("annual_revenue"),
            "funding_raised"          : profile.get("funding_raised"),
            "funding_stage"           : profile.get("funding_stage"),
            "employee_count"          : profile.get("employee_count"),
            "year_founded"            : profile.get("year_founded"),
            "names_of_founders"       : profile.get("names_of_founders", []),
            "c_suite"                 : profile.get("c-suite_officer", []),
            "exact_hq_location"       : profile.get("exact_hq_location"),
            "locations_operating_in"  : profile.get("locations_operating_in", []),
            "competitors"             : profile.get("competitors", []),
            "milestones"              : profile.get("milestones", []),
            "new_features_launched"   : profile.get("new_features_launched", []),
            "recent_partnerships"     : profile.get("recent_partnerships_and_integrations", []),
            "tech_stack_highlights"   : profile.get("tech_stack_highlights", []),
        }

        # Phase B intelligence
        phase_b_keys = [
            "current_problems_struggling_with", "user_complaints",
            "differentiators", "strategic_moves",
            "regulatory_and_legal_issues", "market_sentiment",
            "other_crucial_details",
        ]
        phase_b = {k: profile.get(k) for k in phase_b_keys if profile.get(k)}
        if phase_b:
            kb["company_intelligence"] = phase_b

    # ── App Store Metadata (ratings, installs — useful signal) ────────────────
    ps_meta = (
        db_doc.get("data_sources", {})
        .get("play_store", {})
        .get("extracted_data", {})
        .get("metadata", {})
    )
    as_meta = (
        db_doc.get("data_sources", {})
        .get("app_store", {})
        .get("extracted_data", {})
        .get("metadata", {})
    )
    store_signals = {}
    if ps_meta.get("score"):
        store_signals["play_store"] = {
            "rating"  : ps_meta.get("score"),
            "ratings_count": ps_meta.get("ratings"),
            "installs": ps_meta.get("installs"),
            "reviews_count": ps_meta.get("reviews"),
        }
    if as_meta.get("score"):
        store_signals["app_store"] = {
            "rating": as_meta.get("score"),
            "ratings_count": as_meta.get("userRatingCount"),
        }
    if store_signals:
        kb["store_signals"] = store_signals

    # ── Agent 2: Validated Problems ───────────────────────────────────────────
    agent2 = db_doc.get("agent2_output", {})
    if agent2.get("problems"):
        kb["validated_problems"] = {
            "problems"          : agent2["problems"],
            "total"             : agent2.get("total_problems", len(agent2["problems"])),
            "top_categories"    : agent2.get("top_categories", []),
            "high_severity_count": agent2.get("high_severity_count", 0),
            "data_sources_used" : agent2.get("data_sources_used", []),
        }
    else:
        kb["validated_problems"] = {"note": "Agent 2 has not run yet."}

    # ── Agent 3: Strategic Insights ───────────────────────────────────────────
    agent3 = db_doc.get("agent3_output", {})
    if agent3.get("insights"):
        kb["strategic_insights"] = {
            "insights"           : agent3["insights"],
            "total"              : agent3.get("total_insights", len(agent3["insights"])),
            "critical_count"     : agent3.get("critical_count", 0),
            "dominant_theme"     : agent3.get("dominant_theme", ""),
            "key_strategic_risk" : agent3.get("key_strategic_risk", ""),
            "biggest_opportunity": agent3.get("biggest_opportunity", ""),
        }
    else:
        kb["strategic_insights"] = {"note": "Agent 3 has not run yet."}

    # ── Agent 4: Product Briefs ───────────────────────────────────────────────
    agent4 = db_doc.get("agent4_output", {})
    if agent4.get("briefs"):
        kb["product_briefs"] = {
            "briefs"                  : agent4["briefs"],
            "total"                   : agent4.get("total_briefs", len(agent4["briefs"])),
            "recommended_sprint_focus": agent4.get("recommended_sprint_focus", ""),
            "deferred"                : agent4.get("deferred", []),
        }
    else:
        kb["product_briefs"] = {"note": "Agent 4 has not run yet."}

    # ── Processing Status (lets Copilot warn if data is incomplete) ───────────
    kb["pipeline_status"] = processing_status

    return kb


def _kb_to_string(kb: dict, max_chars: int = 80_000) -> str:
    """Convert knowledge base to a compact JSON string, trimmed to budget."""
    kb_str = json.dumps(kb, ensure_ascii=False, indent=2)
    if len(kb_str) > max_chars:
        # Trim from the middle — keep company profile and problems intact,
        # trim the raw problems list which is the largest section
        kb_trimmed = dict(kb)
        if "validated_problems" in kb_trimmed:
            problems = kb_trimmed["validated_problems"].get("problems", [])
            if len(problems) > 10:
                kb_trimmed["validated_problems"]["problems"] = problems[:10]
                kb_trimmed["validated_problems"]["_trimmed"] = f"Showing 10 of {len(problems)} problems"
        kb_str = json.dumps(kb_trimmed, ensure_ascii=False, indent=2)

        # If still too long, do a hard cut
        if len(kb_str) > max_chars:
            kb_str = kb_str[:max_chars] + "\n... [knowledge base truncated for token budget]"

    return kb_str


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(company_name: str, kb_str: str) -> str:
    return f"""You are a Product Research Copilot for {company_name}.

You have access to a complete research knowledge base compiled from:
- Company profile (founding, funding, team, positioning, pricing, competitors)
- Phase B intelligence (problems, complaints, differentiators, strategic moves, market sentiment)
- Validated user problems extracted from Play Store / App Store / Reddit reviews (Agent 2)
- Strategic insights with root causes and competitor gaps (Agent 3)
- Product briefs with user flows, success metrics, and risks (Agent 4)

STRICT RULES:
1. Answer ONLY from the knowledge base below. Do NOT use general knowledge about this company.
2. If the knowledge base does not contain an answer, say: "This data is not in the research yet."
3. Always cite which source your answer comes from:
   e.g. "(Agent 2 — P003)" or "(company_profile — pricing_tiers)" or "(Agent 3 — I002)"
4. When asked for a recommendation, reference specific data points — never give generic advice.
5. If an agent hasn't run yet, tell the user which agent they need to run first.
6. Keep answers focused. If the question is broad, ask for clarification before writing a wall of text.
7. For questions about problems, always mention severity and frequency.
8. For questions about competitors, always name the specific competitor from the data.

KNOWLEDGE BASE:
{kb_str}"""


# ─────────────────────────────────────────────────────────────────────────────
# CORE Q&A FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def ask_copilot(
    project_name: str,
    question: str,
    provider: str = "gemini",
    conversation_history: Optional[list] = None,
    db_doc: Optional[dict] = None,
) -> tuple[str, list]:
    """
    Answer a single question using the full research knowledge base.

    Args:
        project_name:         Project folder name in database_mock/
        question:             The user's question
        provider:             LLM provider to use
        conversation_history: List of {"role": "user"|"assistant", "content": str}
                              Pass this back each call for multi-turn conversation.
        db_doc:               Pre-loaded db_document (avoids re-reading disk each turn)

    Returns:
        (answer_str, updated_conversation_history)
    """
    if db_doc is None:
        db_doc = load_db_document(project_name)

    kb     = _build_knowledge_base(db_doc)
    kb_str = _kb_to_string(kb)

    company_name  = kb.get("company_profile", {}).get("company_name") or project_name
    system_prompt = _build_system_prompt(company_name, kb_str)

    # Build conversation history for multi-turn support
    history = conversation_history or []
    history.append({"role": "user", "content": question})

    # call_llm with conversation history
    # If call_llm supports messages=, pass history; otherwise fall back to single prompt
    try:
        raw_response = call_llm(
            prompt=question,
            system_prompt=system_prompt,
            provider=provider,
            json_mode=False,
            messages=history,   # multi-turn support — call_llm passes this if supported
        )
    except TypeError:
        # Fallback if call_llm doesn't accept messages= parameter
        # Build conversation context into the prompt manually
        history_text = ""
        if len(history) > 1:
            prev = history[:-1]
            history_text = "\n\n── CONVERSATION SO FAR ──\n"
            for turn in prev[-6:]:  # last 3 exchanges
                role = "You asked" if turn["role"] == "user" else "Copilot answered"
                history_text += f"{role}: {turn['content'][:300]}\n"
            history_text += "── END HISTORY ──\n\n"

        raw_response = call_llm(
            prompt=history_text + question,
            system_prompt=system_prompt,
            provider=provider,
            json_mode=False,
        )

    answer = raw_response.strip()
    history.append({"role": "assistant", "content": answer})

    return answer, history


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE SESSION
# ─────────────────────────────────────────────────────────────────────────────

def run_agent5(project_name: str, provider: str = "gemini") -> None:
    """
    Run an interactive Copilot session for the given project.
    Type 'exit' or 'quit' to end the session.
    Type 'status' to see which agents have run.
    Type 'clear' to reset conversation history.
    """
    log.info(f"[Agent 5] Starting Copilot session for project: {project_name}")

    # Load once — reuse across all turns
    db_doc = load_db_document(project_name)
    kb     = _build_knowledge_base(db_doc)

    company_name = kb.get("company_profile", {}).get("company_name") or project_name
    status       = db_doc.get("processing_status", {})

    print("\n" + "=" * 60)
    print(f"  Product Research Copilot — {company_name}")
    print("=" * 60)
    print(f"  Agent 2 (Problems)  : {'✅ Done' if status.get('agent2_insights_extracted') else '⚠️  Not run'}")
    print(f"  Agent 3 (Insights)  : {'✅ Done' if status.get('agent3_synthesis_done') else '⚠️  Not run'}")
    print(f"  Agent 4 (Briefs)    : {'✅ Done' if status.get('agent4_product_brief_done') else '⚠️  Not run'}")
    print("─" * 60)
    print("  Ask anything about the research. Type 'exit' to quit.")
    print("  Commands: 'status' | 'clear' | 'exit'")
    print("=" * 60)

    # Starter prompts to guide the user
    print("\n  💡 Try asking:")
    print("     • What is the biggest trust problem users face?")
    print("     • How does our pricing compare to competitors?")
    print("     • Which product brief should we build first and why?")
    print("     • What are users saying about [feature]?")
    print("     • What is the root cause of the onboarding drop-off?")
    print()

    conversation_history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Copilot session ended.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            print("  Copilot session ended.")
            break

        if user_input.lower() == "clear":
            conversation_history = []
            print("  Conversation history cleared.\n")
            continue

        if user_input.lower() == "status":
            print(f"\n  Pipeline status for {company_name}:")
            for k, v in status.items():
                icon = "✅" if v else "⚠️ "
                print(f"  {icon} {k}: {v}")
            print()
            continue

        print(f"\n  Thinking...\n")

        answer, conversation_history = ask_copilot(
            project_name=project_name,
            question=user_input,
            provider=provider,
            conversation_history=conversation_history,
            db_doc=db_doc,
        )

        print(f"Copilot: {answer}\n")
        print("─" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  AGENT 5: PRODUCT RESEARCH COPILOT")
    print("=" * 60)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent5(project, provider=provider)