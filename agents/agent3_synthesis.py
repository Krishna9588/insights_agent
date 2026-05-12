"""
agent3_synthesis.py
===================
Agent 3 — Research Synthesis

Reads:  database_mock/{project_name}/db_document.json  (needs agent2_output populated)
Writes: agent3_output block back into the same db_document.json

Callable from other scripts:
    from agent3_synthesis import run_agent3
    result = run_agent3("Groww", provider="gemini")

Data flow alignment:
    Consumes  → agent2_output.problems[]
                  .problem_id, .problem, .evidence, .frequency, .user_type,
                  .category, .competitor_has_same_issue, .severity
    Produces  → agent3_output.insights[]
                  .insight_id, .insight, .supporting_problem_ids, .root_cause,
                  .evidence_summary, .competitor_gap, .opportunity_size,
                  .implication, .priority, .theme, .confidence
                agent3_output.company_snapshot  ← passed to Agent 4 as context
"""

import os
import json
import logging

from model_connect import call_llm

log = logging.getLogger("agent3")
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
            f"No db_document found for '{project_name}'. Run Agent 1 first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db_document(project_name: str, doc: dict) -> str:
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=4, ensure_ascii=False)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT BUILDER
# Assembles the full company context that Agent 3 needs to synthesise insights
# that are grounded in REAL competitive and positioning data — not assumptions.
# Old version only passed 4 fields. This passes everything.
# ─────────────────────────────────────────────────────────────────────────────

def _build_company_context(db_doc: dict) -> dict:
    """
    Extract the full company intelligence snapshot from db_document.
    This becomes both the LLM context and is saved as agent3_output.company_snapshot
    so Agent 4 can use it directly without re-reading data_sources.
    """
    cp = db_doc.get("data_sources", {}).get("company_profile", {})
    profile = cp.get("data") or cp

    if not isinstance(profile, dict):
        return {}

    return {
        # ── Identity & market position ───────────────────────────────────────
        "company_name"            : profile.get("company_name"),
        "domain"                  : profile.get("domain"),
        "industry_and_segment"    : profile.get("industry_and_segment"),
        "key_positioning"         : profile.get("key_positioning"),
        "available_platforms"     : profile.get("available_platforms"),
        "target_customer_segments": profile.get("target_customer_segments", []),

        # ── Business model ───────────────────────────────────────────────────
        "revenue_model"           : profile.get("revenue_model"),
        "pricing_tiers"           : profile.get("pricing_tiers", []),
        "funding_stage"           : profile.get("funding_stage"),
        "funding_raised"          : profile.get("funding_raised"),
        "annual_revenue"          : profile.get("annual_revenue"),
        "no_of_users"             : profile.get("no_of_users"),
        "employee_count"          : profile.get("employee_count"),

        # ── Competitive landscape ────────────────────────────────────────────
        "competitors"             : profile.get("competitors", [])[:4],
        "differentiators"         : profile.get("differentiators", [])[:5],
        "market_sentiment"        : profile.get("market_sentiment", {}),

        # ── Strategic signals ────────────────────────────────────────────────
        "strategic_moves"         : profile.get("strategic_moves", [])[:5],
        "recent_partnerships"     : profile.get("recent_partnerships_and_integrations", [])[:4],
        "new_features_launched"   : profile.get("new_features_launched", [])[:6],
        "milestones"              : profile.get("milestones", []),

        # ── Risk signals ─────────────────────────────────────────────────────
        "regulatory_and_legal"    : profile.get("regulatory_and_legal_issues", [])[:4],

        # ── Other context ────────────────────────────────────────────────────
        "other_crucial_details"   : profile.get("other_crucial_details", [])[:4],
    }


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Principal Product Strategist synthesising research into sharp strategic insights.

Rules:
- An insight is NOT a problem restatement. It reveals a root cause, market gap, or structural pattern.
- Group 2-4 related problems into one insight. Never create one insight per problem.
- competitor_gap must reference a SPECIFIC competitor from the competitors list — not a generic statement.
- opportunity_size must reference company-specific data: pricing, user count, segment size, or revenue.
- confidence must reflect how much direct evidence exists: High = multiple sources, Medium = one clear source, Low = inferred.
- Every insight must be specific enough that a founder can decide on it this quarter.
- Return ONLY valid JSON. No markdown. No explanation."""


def _build_prompt(
    project_name: str,
    problems: list,
    company_context: dict,
    agent2_meta: dict,
) -> str:
    problems_str = json.dumps(problems, ensure_ascii=False)
    context_str  = json.dumps(company_context, ensure_ascii=False)

    # Trim to avoid token overflow — context is prioritised, problems second
    if len(context_str) > 15_000:
        context_str = context_str[:15_000] + "... [truncated]"
    if len(problems_str) > 60_000:
        problems_str = problems_str[:60_000] + "... [truncated]"

    company_name   = company_context.get("company_name") or project_name
    top_categories = agent2_meta.get("top_categories", [])
    high_sev_count = agent2_meta.get("high_severity_count", 0)
    sources_used   = agent2_meta.get("data_sources_used", [])

    return f"""Synthesise research insights for: {company_name} (project: {project_name})

── AGENT 2 SUMMARY ──────────────────────────────────────────
Total problems found : {len(problems)}
High severity count  : {high_sev_count}
Top categories       : {top_categories}
Data sources used    : {sources_used}

── FULL COMPANY CONTEXT ─────────────────────────────────────
{context_str}

── VALIDATED USER PROBLEMS (from Agent 2) ───────────────────
{problems_str}

── YOUR TASK ────────────────────────────────────────────────
Synthesise 5-8 strategic insights. For each:
1. Group 2-4 related problems (reference their problem_ids).
2. Identify the ROOT CAUSE — structural, not symptomatic.
3. Find the COMPETITOR GAP — what does a specific named competitor do differently?
   Use the competitors list in company context. If no competitor data exists, say "Insufficient data".
4. State the OPPORTUNITY SIZE — reference a specific number from context (users, revenue, pricing tier).
5. State your CONFIDENCE level based on how much direct evidence supports this insight.

Return this exact JSON:
{{
  "insights": [
    {{
      "insight_id": "I001",
      "insight": "One sharp sentence — the core strategic finding",
      "supporting_problem_ids": ["P001", "P003"],
      "root_cause": "Why this problem exists structurally — what design, business, or market decision caused it",
      "evidence_summary": "2-3 sentences summarising the strongest evidence from Agent 2",
      "competitor_gap": "What [specific competitor name] does or fails to do here — be precise",
      "opportunity_size": "Quantified opportunity using real company data — e.g. affects X% of paid tier users",
      "implication": "What this means for the product roadmap — what should change and why now",
      "priority": "Critical | High | Medium",
      "theme": "Trust | Discovery | Education | Workflow | Pricing | Performance | Retention | Onboarding | Other",
      "confidence": "High | Medium | Low"
    }}
  ],
  "total_insights": 0,
  "critical_count": 0,
  "high_count": 0,
  "dominant_theme": "The single most important theme across all insights",
  "key_strategic_risk": "The one thing that could seriously harm this company if unaddressed",
  "biggest_opportunity": "The single highest-leverage product move available right now"
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent3(project_name: str, provider: str = "gemini") -> dict:
    """
    Run Agent 3 for the given project.

    Args:
        project_name: Must match a folder in database_mock/
        provider:     "gemini" | "gemini_2" | "claude" | "openai"

    Returns:
        The parsed agent3_output dict with an "insights" list.
    """
    log.info(f"[Agent 3] Starting for project: {project_name}")

    # 1. Load
    db_doc = load_db_document(project_name)

    # 2. Check Agent 2 ran
    agent2_out = db_doc.get("agent2_output", {})
    problems   = agent2_out.get("problems", [])
    if not problems:
        raise ValueError(
            "agent2_output.problems is empty. Run Agent 2 first.\n"
            f"processing_status: {db_doc.get('processing_status')}"
        )
    log.info(f"Loaded {len(problems)} problems from Agent 2.")

    # Agent 2 metadata — passed to prompt for context
    agent2_meta = {
        "top_categories"    : agent2_out.get("top_categories", []),
        "high_severity_count": agent2_out.get("high_severity_count", 0),
        "data_sources_used" : agent2_out.get("data_sources_used", []),
    }

    # 3. Build full company context
    log.info("Building company context from data_sources...")
    company_context = _build_company_context(db_doc)
    if not company_context:
        log.warning("Company context is empty — insights will lack competitive grounding.")

    # 4. Build prompt and call LLM
    prompt = _build_prompt(project_name, problems, company_context, agent2_meta)
    log.info(f"Prompt size: {len(prompt):,} chars. Calling {provider.upper()}...")

    raw_response = call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        provider=provider,
        json_mode=True,
    )

    # 5. Parse
    try:
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()

        result = json.loads(clean)
        insights = result.get("insights", [])

        if not insights:
            log.warning("LLM returned 0 insights. Raw snippet:")
            log.warning(raw_response[:500])

        log.info(f"Generated {len(insights)} insights.")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        log.error(f"Raw response:\n{raw_response[:800]}")
        return {"status": "error", "message": f"JSON parse failed: {e}", "raw": raw_response[:500]}

    # 6. Attach company_snapshot so Agent 4 doesn't need to re-read data_sources
    result["company_snapshot"] = company_context

    # 7. Save
    db_doc["agent3_output"] = result
    db_doc["processing_status"]["agent3_synthesis_done"] = True
    save_db_document(project_name, db_doc)

    log.info(f"[Agent 3] Done. {len(insights)} insights saved.")

    print("\n" + "=" * 55)
    print(f"  Agent 3 Complete — {project_name}")
    print(f"  Insights generated : {len(insights)}")
    print(f"  Critical           : {result.get('critical_count', 0)}")
    print(f"  High               : {result.get('high_count', 0)}")
    print(f"  Dominant theme     : {result.get('dominant_theme', '?')}")
    print(f"  Key strategic risk : {result.get('key_strategic_risk', '?')[:70]}")
    print(f"  Biggest opportunity: {result.get('biggest_opportunity', '?')[:70]}")
    print("=" * 55)
    for i in insights[:5]:
        print(
            f"  [{i.get('priority','?')}] [{i.get('confidence','?')} conf] "
            f"{i.get('insight','')[:75]}"
        )
    if len(insights) > 5:
        print(f"  ... and {len(insights) - 5} more")
    print()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AGENT 3: RESEARCH SYNTHESIS")
    print("=" * 55)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent3(project, provider=provider)