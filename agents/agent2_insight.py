"""
agent2_insight.py
=================
Agent 2 — User Problem Extraction

Reads:  database_mock/{project_name}/db_document.json  (written by Agent 1)
Writes: agent2_output block back into the same db_document.json

Callable from other scripts:
    from agent2_insight import run_agent2
    result = run_agent2("Groww", provider="gemini")
"""

import os
import json
import logging
from typing import Optional

from model_connect import call_llm

log = logging.getLogger("agent2")
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
            f"Run Agent 1 first. Expected path: {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db_document(project_name: str, doc: dict) -> str:
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=4, ensure_ascii=False)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# DATA EXTRACTION
# Pulls every useful signal from the db_document into a clean dict.
# Key fixes vs old version:
#   - Correct review path: play_store.extracted_data.reviews (not play_store.reviews)
#   - Pass full company_profile Phase B data (already structured — don't waste it)
#   - Include 3-star reviews (not just 1-2 star) — patterns hide there too
#   - Include app store description as a signal source
#   - Include Reddit and YouTube if present
# ─────────────────────────────────────────────────────────────────────────────

def _get_reviews(store_data: dict, star_threshold: int = 3, limit: int = 50) -> list:
    """
    Safely extract reviews from either play_store or app_store data.
    Handles the nested extracted_data.reviews path that Agent 1 produces.
    """
    reviews = []

    # Path 1: new scraper format — extracted_data.reviews
    reviews = (
        store_data.get("extracted_data", {}).get("reviews")
        or store_data.get("reviews")           # legacy flat format
        or store_data.get("data", {}).get("reviews", [])
    )

    if not reviews:
        return []

    filtered = []
    for r in reviews:
        # Support both field name conventions
        rating = r.get("rating") or r.get("score") or 5
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = 5

        if rating <= star_threshold:
            text = r.get("content") or r.get("text") or r.get("review") or ""
            if len(text.strip()) > 20:  # skip empty/near-empty reviews
                filtered.append({
                    "rating": rating,
                    "text"  : text.strip()[:400],
                    "date"  : r.get("date", ""),
                })

    # Sort by rating ascending (worst first) then trim
    filtered.sort(key=lambda x: x["rating"])
    return filtered[:limit]


def _extract_signals(data_sources: dict) -> dict:
    """
    Extract every high-signal input from all data sources.
    Returns a structured dict that fits comfortably in one LLM call.
    """
    signals = {}

    # ── Company Profile (Phase A + Phase B from company_profile_researcher) ──
    cp = data_sources.get("company_profile", {})
    profile = cp.get("data") or cp  # handle both wrapped and flat formats

    if isinstance(profile, dict) and not profile.get("error"):
        # Phase A: core company facts — give Agent 2 full context
        signals["company_context"] = {
            "company_name"           : profile.get("company_name"),
            "industry_and_segment"   : profile.get("industry_and_segment"),
            "key_positioning"        : profile.get("key_positioning"),
            "revenue_model"          : profile.get("revenue_model"),
            "pricing_tiers"          : profile.get("pricing_tiers", []),
            "target_customer_segments": profile.get("target_customer_segments", []),
            "no_of_users"            : profile.get("no_of_users"),
            "funding_stage"          : profile.get("funding_stage"),
            "available_platforms"    : profile.get("available_platforms"),
            "competitors"            : profile.get("competitors", [])[:4],
            "milestones"             : profile.get("milestones", []),
        }

        # Phase B: structured problems and intelligence (already extracted — use it)
        phase_b = {}

        if profile.get("current_problems_struggling_with"):
            phase_b["researched_problems"] = profile["current_problems_struggling_with"][:8]

        if profile.get("user_complaints"):
            phase_b["researched_complaints"] = profile["user_complaints"][:8]

        if profile.get("differentiators"):
            phase_b["differentiators"] = profile["differentiators"][:5]

        if profile.get("strategic_moves"):
            phase_b["strategic_moves"] = profile["strategic_moves"][:5]

        if profile.get("market_sentiment"):
            phase_b["market_sentiment"] = profile["market_sentiment"]

        if profile.get("regulatory_and_legal_issues"):
            phase_b["regulatory_issues"] = profile["regulatory_and_legal_issues"][:5]

        if profile.get("recent_partnerships_and_integrations"):
            phase_b["partnerships"] = profile["recent_partnerships_and_integrations"][:5]

        if profile.get("new_features_launched"):
            phase_b["recent_features"] = profile["new_features_launched"][:6]

        if profile.get("other_crucial_details"):
            phase_b["other_details"] = profile["other_crucial_details"][:5]

        if phase_b:
            signals["company_intelligence"] = phase_b

        # App store listing descriptions are signal-rich (explains positioning gaps)
        ps_meta = data_sources.get("play_store", {}).get("extracted_data", {}).get("metadata", {})
        as_meta = data_sources.get("app_store",  {}).get("extracted_data", {}).get("metadata", {})
        app_info = {}
        if ps_meta.get("score"):
            app_info["play_store_rating"] = ps_meta["score"]
            app_info["play_store_total_ratings"] = ps_meta.get("ratings")
            app_info["play_store_installs"]       = ps_meta.get("installs")
        if as_meta.get("score"):
            app_info["app_store_rating"] = as_meta["score"]
        if app_info:
            signals["app_store_metadata"] = app_info

    # ── Play Store Reviews (≤3 stars) ─────────────────────────────────────────
    ps = data_sources.get("play_store", {})
    if isinstance(ps, dict) and ps.get("extraction_metadata", {}).get("status") != "error":
        reviews = _get_reviews(ps, star_threshold=3, limit=50)
        if reviews:
            signals["play_store_reviews"] = reviews
            log.info(f"  Play Store: {len(reviews)} reviews (≤3 stars) extracted")

    # ── App Store Reviews (≤3 stars) ──────────────────────────────────────────
    ap = data_sources.get("app_store", {})
    if isinstance(ap, dict) and ap.get("extraction_metadata", {}).get("status") != "error":
        reviews = _get_reviews(ap, star_threshold=3, limit=30)
        if reviews:
            signals["app_store_reviews"] = reviews
            log.info(f"  App Store: {len(reviews)} reviews (≤3 stars) extracted")

    # ── Reddit Posts ──────────────────────────────────────────────────────────
    rd = data_sources.get("reddit", {})
    if isinstance(rd, dict):
        all_posts = []
        # Agent 1 stores reddit as {"reddit_0": {...}, "reddit_1": {...}}
        for key, val in rd.items():
            if isinstance(val, dict):
                posts = (
                    val.get("posts")
                    or val.get("data", {}).get("posts", [])
                )
                all_posts.extend(posts or [])

        if all_posts:
            signals["reddit_posts"] = [
                {
                    "title": p.get("title", ""),
                    "body" : str(p.get("selftext", p.get("body", "")))[:400],
                    "score": p.get("score", 0),
                    "comments_count": p.get("num_comments", 0),
                }
                for p in all_posts[:25]
                if p.get("title") or p.get("selftext") or p.get("body")
            ]
            log.info(f"  Reddit: {len(signals['reddit_posts'])} posts extracted")

    # ── YouTube Comments ──────────────────────────────────────────────────────
    yt = data_sources.get("youtube", {})
    if isinstance(yt, dict):
        all_comments = []
        for key, val in yt.items():
            if isinstance(val, dict):
                comments = (
                    val.get("comments")
                    or val.get("data", {}).get("comments", [])
                )
                all_comments.extend(comments or [])

        if all_comments:
            signals["youtube_comments"] = [
                str(c.get("text", c.get("comment", "")))[:300]
                for c in all_comments[:30]
                if c.get("text") or c.get("comment")
            ]
            log.info(f"  YouTube: {len(signals['youtube_comments'])} comments extracted")

    # ── Internal Transcripts ──────────────────────────────────────────────────
    it = data_sources.get("internal_transcripts", {})
    if isinstance(it, dict) and it.get("status") != "error":
        signal_items = it.get("signals", [])
        if signal_items:
            signals["internal_signals"] = [
                {
                    "type"   : s.get("signal_type", ""),
                    "content": s.get("content", ""),
                    "conf"   : s.get("confidence", 0),
                }
                for s in signal_items[:30]
            ]
            log.info(f"  Internal transcripts: {len(signals['internal_signals'])} signals")

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Senior Product Researcher extracting VALIDATED USER PROBLEMS.

Rules:
- Every problem must be supported by direct evidence from the data provided.
- Group near-identical complaints into one problem. No duplicates.
- Do NOT ignore 3-star reviews — they often contain the clearest problem descriptions.
- Use the company_context to understand what the product promises vs what users actually experience.
- If company_intelligence contains researched_problems or researched_complaints, treat them as
  high-confidence evidence and incorporate them — do not re-derive what's already proven.
- Identify problems that are UNIQUE to this company vs industry-wide issues.
- Do NOT invent data. Every problem must trace to a specific evidence source.

Return ONLY valid JSON matching the schema. No markdown. No explanation."""


def _build_prompt(project_name: str, signals: dict) -> str:
    signals_str = json.dumps(signals, ensure_ascii=False)

    if len(signals_str) > 100_000:
        signals_str = signals_str[:100_000] + "\n... [truncated for length]"

    company_name = (
        signals.get("company_context", {}).get("company_name")
        or project_name
    )

    return f"""Extract validated user problems for: {company_name} (project: {project_name})

SOURCE DATA:
{signals_str}

Instructions:
1. Read company_context first to understand what the product promises.
2. If company_intelligence.researched_problems exists, use those directly as anchors.
3. Cross-reference with play_store_reviews, app_store_reviews, reddit_posts for raw evidence.
4. For each problem, identify if competitors have the same issue (competitor_has_same_issue: true/false).

Return this exact JSON:
{{
  "problems": [
    {{
      "problem_id": "P001",
      "problem": "One precise sentence — what the user cannot do or experiences badly",
      "evidence": [
        "Direct quote or close paraphrase from source data"
      ],
      "frequency": "Low | Medium | High",
      "user_type": "Beginner | Intermediate | Advanced | All",
      "source_mix": ["Play Store", "App Store", "Reddit", "YouTube", "Company Research", "Internal"],
      "category": "Onboarding | Core Feature | Performance | Trust | Pricing | Support | Content | Other",
      "competitor_has_same_issue": false,
      "severity": "Critical | High | Medium | Low"
    }}
  ],
  "total_problems": 0,
  "top_categories": [],
  "high_severity_count": 0,
  "data_sources_used": []
}}

Find AT LEAST 10 problems if the data supports it.
Sort by severity descending, then frequency descending."""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent2(project_name: str, provider: str = "gemini") -> dict:
    """
    Run Agent 2 for the given project.

    Args:
        project_name: Must match a folder in database_mock/
        provider:     "gemini" | "gemini_2" | "claude" | "openai"

    Returns:
        The parsed agent2_output dict with a "problems" list.
    """
    log.info(f"[Agent 2] Starting for project: {project_name}")

    # 1. Load
    log.info("Loading db_document from Agent 1...")
    db_doc = load_db_document(project_name)
    data_sources = db_doc.get("data_sources", {})

    if not data_sources:
        log.error("data_sources is empty — Agent 1 may not have run successfully.")
        return {"status": "error", "message": "No data_sources in db_document"}

    # 2. Extract signals
    log.info("Extracting signals from all data sources...")
    signals = _extract_signals(data_sources)
    log.info(f"Signal keys found: {list(signals.keys())}")

    if not signals:
        log.error("No usable signals extracted. Check that Agent 1 produced real data.")
        return {"status": "error", "message": "No signals could be extracted from data_sources"}

    # 3. Build prompt and call LLM
    prompt = _build_prompt(project_name, signals)
    log.info(f"Prompt size: {len(prompt):,} chars. Calling {provider.upper()}...")

    raw_response = call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        provider=provider,
        json_mode=True,
    )

    # 4. Parse response
    try:
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()

        result = json.loads(clean)
        problems = result.get("problems", [])

        if not problems:
            log.warning("LLM returned 0 problems. Raw response snippet:")
            log.warning(raw_response[:500])

        log.info(f"Extracted {len(problems)} problems.")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        log.error(f"Raw response (first 800 chars):\n{raw_response[:800]}")
        return {
            "status" : "error",
            "message": f"JSON parse failed: {e}",
            "raw"    : raw_response[:500],
        }

    # 5. Save back to db_document
    db_doc["agent2_output"] = result
    db_doc["processing_status"]["agent2_insights_extracted"] = True
    save_db_document(project_name, db_doc)

    log.info(f"[Agent 2] Done. {len(problems)} problems saved to db_document.")

    print("\n" + "=" * 55)
    print(f"  Agent 2 Complete — {project_name}")
    print(f"  Problems found    : {len(problems)}")
    print(f"  High severity     : {result.get('high_severity_count', 0)}")
    print(f"  Top categories    : {result.get('top_categories', [])}")
    print(f"  Sources used      : {result.get('data_sources_used', [])}")
    print("=" * 55)
    for p in problems[:6]:
        print(f"  [{p.get('severity','?')}] [{p.get('frequency','?')}] {p.get('problem','')[:80]}")
    if len(problems) > 6:
        print(f"  ... and {len(problems) - 6} more")
    print()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AGENT 2: PROBLEM EXTRACTION")
    print("=" * 55)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent2(project, provider=provider)