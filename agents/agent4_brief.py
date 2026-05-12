"""
agent4_brief.py
===============
Agent 4 — Product Brief Generation

Reads:  database_mock/{project_name}/db_document.json  (needs agent3_output populated)
Writes: agent4_output block back into the same db_document.json

Callable from other scripts:
    from agent4_brief import run_agent4
    result = run_agent4("Groww", provider="gemini")
"""

import os
import json
import logging

from model_connect import call_llm

log = logging.getLogger("agent4")
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
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Senior Product Manager converting research insights into buildable feature briefs.

Rules:
- Each brief solves exactly ONE insight. No feature bloat.
- User flows: 3-5 steps maximum. Be concrete, not abstract.
- Effort and priority must reflect real engineering complexity.
- Expected impact must be measurable — reference a specific metric or user action.
- Return ONLY valid JSON. No markdown. No explanation."""


def _build_prompt(project_name: str, insights: list) -> str:
    # Only feed Critical + High priority — rest is noise for briefs
    priority_insights = [
        i for i in insights
        if i.get("priority") in ("Critical", "High")
    ] or insights  # fallback to all if none qualify

    insights_str = json.dumps(priority_insights, ensure_ascii=False)

    return f"""Create product feature briefs for: {project_name}

INSIGHTS TO ADDRESS:
{insights_str}

Generate 4-6 actionable product briefs. Start with Critical priority insights.

Return this exact JSON structure:
{{
  "briefs": [
    {{
      "brief_id": "B001",
      "feature_name": "Short memorable name",
      "addresses_insight": "I001",
      "problem": "The specific user friction this solves (1-2 sentences)",
      "why_now": "Why this should be built this sprint, not next quarter",
      "solution": "What the feature does — concrete, not abstract (2-3 sentences)",
      "user_flow": [
        "Step 1: User lands on X screen",
        "Step 2: User taps Y button",
        "Step 3: System shows Z result"
      ],
      "expected_impact": "Specific measurable outcome, e.g. reduces drop-off at KYC by ~30%",
      "effort": "Low | Medium | High",
      "priority": "P0 | P1 | P2",
      "success_metric": "The one number that tells you this feature is working"
    }}
  ],
  "total_briefs": 0,
  "recommended_sprint_focus": "Which single brief to start with and the one-line reason why"
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent4(project_name: str, provider: str = "gemini") -> dict:
    """
    Run Agent 4 for the given project.

    Args:
        project_name: Must match a folder in database_mock/
        provider:     "gemini" | "gemini_2" | "claude" | "openai"

    Returns:
        The parsed agent4_output dict with a "briefs" list.
    """
    log.info(f"[Agent 4] Starting for project: {project_name}")

    # 1. Load
    db_doc = load_db_document(project_name)

    # 2. Check Agent 3 ran
    agent3_out = db_doc.get("agent3_output", {})
    insights   = agent3_out.get("insights", [])
    if not insights:
        raise ValueError(
            "agent3_output.insights is empty. Run Agent 3 first.\n"
            f"processing_status: {db_doc.get('processing_status')}"
        )

    log.info(f"Loaded {len(insights)} insights from Agent 3.")

    # 3. Build prompt and call LLM
    prompt = _build_prompt(project_name, insights)
    log.info(f"Prompt size: {len(prompt)} chars. Calling {provider.upper()}...")

    raw_response = call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        provider=provider,
        json_mode=True,
    )

    # 4. Parse
    try:
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()

        result = json.loads(clean)
        briefs = result.get("briefs", [])

        if not briefs:
            log.warning("LLM returned 0 briefs. Raw snippet:")
            log.warning(raw_response[:500])

        log.info(f"Generated {len(briefs)} product briefs.")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        log.error(f"Raw response:\n{raw_response[:800]}")
        return {"status": "error", "message": f"JSON parse failed: {e}", "raw": raw_response[:500]}

    # 5. Save
    db_doc["agent4_output"] = result
    db_doc["processing_status"]["agent4_product_brief_done"] = True
    save_db_document(project_name, db_doc)

    log.info(f"[Agent 4] Done. {len(briefs)} briefs saved.")

    print("\n" + "=" * 55)
    print(f"  Agent 4 Complete — {project_name}")
    print(f"  Briefs generated : {len(briefs)}")
    print(f"  Sprint focus     : {result.get('recommended_sprint_focus', '?')}")
    print("=" * 55)
    for b in briefs:
        print(f"  [{b.get('priority','?')}] {b.get('feature_name','')} — {b.get('effort','?')} effort")
    print()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AGENT 4: PRODUCT BRIEF GENERATION")
    print("=" * 55)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent4(project, provider=provider)