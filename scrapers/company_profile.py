import os
import json
import re
import time
from pathlib import Path
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv

load_dotenv()

# ============================================================
#  CONFIGURATION
# ============================================================
TIME_LIMIT       = 120   # Timeout per API request (seconds)
RETRY_DELAY      = 3.0   # Seconds between API calls
STORAGE_FOLDER   = "data/results"
RAW_FOLDER       = "data/raw"       # Raw/partial responses saved here
MAX_JSON_RETRIES = 2     # Per-model JSON-parse retries (lower = less wasted quota)

# ── Max output tokens ────────────────────────────────────────
# gemini-2.5-flash supports up to 65,536 output tokens.
# A full company profile (5+ items × 4 arrays) = 15,000-25,000 tokens.
# Old value of 8,192 was the root cause of truncation ("no } found").
MAX_OUTPUT_TOKENS = 65536

# ── Model Strategy ───────────────────────────────────────────
# PRIMARY: gemini-2.5-flash / gemini-2.5-flash-lite
#   Most stable. Do NOT support native JSON mode.
#   Schema is embedded in the prompt; we parse the text response ourselves.
#
# STRUCTURED FALLBACKS: support response_mime_type="application/json"
#   API-enforced structured output, cleaner — but hit quota faster.
#   Only reached when all primary models fail.
#
MODELS_PRIMARY = [
    "gemini-2.5-flash",       # Most stable, best quality — schema via prompt
    "gemini-2.5-flash-lite",  # Faster / lower quota — schema via prompt
]

MODELS_STRUCTURED = [
    # Support response_mime_type="application/json" + Google Search
    "gemini-flash-latest",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
]

# Model used ONLY for the repair pass (no Google Search needed — fast & cheap)
REPAIR_MODEL = "gemini-2.5-flash-lite"


# ============================================================
#  OUTPUT SCHEMAS  (split into Phase A + Phase B)
# ============================================================

# ── Phase A: Core company facts (small token budget) ─────────
JSON_SCHEMA_PHASE_A = """
You MUST return ONLY a valid JSON object — no markdown, no text outside the JSON, no code fences.
The very first character must be { and the very last must be }.

Return data in EXACTLY this structure:

{
  "company_name": "string",
  "domain": "string",
  "playstore_link": "string or null",
  "appstore_link": "string or null",
  "youtube_official_channel": "string or null",
  "linkedin_company_page": "string or null",
  "year_founded": "string — include founding city and country",
  "names_of_founders": ["string"],
  "c-suite_officer": ["string — name + title, max 5"],
  "employee_count": "string — e.g. '1,200' or '~5,000' or 'Unable to verify'",
  "exact_hq_location": "string",
  "locations_operating_in": ["string"],
  "industry_and_segment": "string",
  "available_platforms": "one of: Web | Mobile | Web & Mobile (both) | Data not publicly available",
  "funding_raised": "string",
  "funding_stage": "string — e.g. 'Series C', 'Public (NYSE)', 'Bootstrapped', 'Unable to verify'",
  "no_of_users": "string",
  "annual_revenue": "string",
  "key_positioning": "string",
  "revenue_model": "string",
  "pricing_tiers": ["string — one entry per plan, e.g. 'Free: $0/mo — basic features', 'Pro: $49/mo — unlimited X'"],
  "target_customer_segments": ["string — short label, e.g. 'SMB e-commerce', 'Enterprise HR teams'"],
  "tech_stack_highlights": ["string — key technologies, e.g. 'React', 'AWS', 'PostgreSQL'"],
  "milestones": ["string — key company milestones in chronological order"],
  "new_features_launched": ["string — recent product/feature releases, e.g. 'Launched AI assistant — Mar 2025'"],
  "recent_partnerships_and_integrations": [
    {
      "partner": "string — partner or platform name",
      "type": "one of: Integration | Acquisition | Strategic Partnership | Distribution | Investment",
      "description": "string",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent"
    }
  ],
  "competitors": [
    {"name": "string", "domain": "string"}
  ]
}

Rules:
- competitors: max 4
- c-suite_officer: max 5
- pricing_tiers: list every publicly known plan; write [] if none are public
- target_customer_segments: max 5 short labels
- tech_stack_highlights: max 8 items; only include if publicly confirmed
- milestones: max 8 items; focus on funding rounds, launches, acquisitions, user count records
- new_features_launched: max 8 items; most recent first
- recent_partnerships_and_integrations: AT LEAST 3 items from 2023-2026; write [] if none found
- Use Google Search to find verified sources from 2023-2026
- If unverifiable write "Unable to verify" — NEVER fabricate
"""

# ── Phase B: Deep analysis fields (uses Phase A context) ─────
# Token budget: 4 rich arrays (2 at 5+ items, 2 at 3+ items) + 2 lighter arrays + 1 object
JSON_SCHEMA_PHASE_B = """
You MUST return ONLY a valid JSON object — no markdown, no text outside the JSON, no code fences.
The very first character must be { and the very last must be }.

Return data in EXACTLY this structure:

{
  "current_problems_struggling_with": [
    {
      "description": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["short sentence describing impact"]
    }
  ],
  "user_complaints": [
    {
      "issue": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "differentiators": [
    {
      "feature": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "strategic_moves": [
    {
      "move": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "regulatory_and_legal_issues": [
    {
      "issue": "string — describe the lawsuit, fine, compliance problem, or regulatory action",
      "jurisdiction": "string — e.g. 'USA', 'EU', 'India'",
      "status": "one of: Active | Resolved | Under Investigation | Unknown",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string — business or reputational impact"]
    }
  ],
  "market_sentiment": {
    "overall": "one of: Positive | Neutral | Negative | Mixed",
    "analyst_view": "string — 1-2 sentences summarising analyst or press tone",
    "user_community_view": "string — 1-2 sentences on Reddit / review-site / social sentiment",
    "source": "URL string",
    "date": "YYYY-MM-DD or Recent"
  },
  "other_crucial_details": ["string"]
}

Rules:
- current_problems_struggling_with: AT LEAST 5 items — search news, forums, reviews for real evidence
- user_complaints: AT LEAST 5 items — search app store reviews, Reddit, G2, Trustpilot
- differentiators: AT LEAST 3 well-sourced items (quality over quantity)
- strategic_moves: AT LEAST 3 well-sourced items (quality over quantity)
- regulatory_and_legal_issues: include ALL known; write [] if genuinely none found
- Descriptions under 200 characters each
- Use Google Search to find verified sources from 2023-2026
- If unverifiable write "Unable to verify" — NEVER fabricate
- Include exact source URLs for every analysis item
"""

# ── Legacy combined schema — kept for REPAIR PASS only ───────
JSON_SCHEMA_INSTRUCTION = JSON_SCHEMA_PHASE_A.rstrip() + "\n\n" + JSON_SCHEMA_PHASE_B

# Pydantic-style JSON schema for models that support response_schema
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # ── Phase A fields ────────────────────────────────────────
        "company_name":             {"type": "string"},
        "domain":                   {"type": "string"},
        "playstore_link":           {"type": "string"},
        "appstore_link":            {"type": "string"},
        "youtube_official_channel": {"type": "string"},
        "linkedin_company_page":    {"type": "string"},
        "year_founded":             {"type": "string"},
        "names_of_founders":        {"type": "array", "items": {"type": "string"}},
        "c-suite_officer":          {"type": "array", "items": {"type": "string"}},
        "employee_count":           {"type": "string"},
        "exact_hq_location":        {"type": "string"},
        "locations_operating_in":   {"type": "array", "items": {"type": "string"}},
        "industry_and_segment":     {"type": "string"},
        "available_platforms":      {"type": "string",
                                     "enum": ["Web", "Mobile", "Both",
                                              "Data not publicly available"]},
        "funding_raised":           {"type": "string"},
        "funding_stage":            {"type": "string"},
        "no_of_users":              {"type": "string"},
        "annual_revenue":           {"type": "string"},
        "key_positioning":          {"type": "string"},
        "revenue_model":            {"type": "string"},
        "pricing_tiers":            {"type": "array", "items": {"type": "string"}},
        "target_customer_segments": {"type": "array", "items": {"type": "string"}},
        "tech_stack_highlights":    {"type": "array", "items": {"type": "string"}},
        "milestones":               {"type": "array", "items": {"type": "string"}},
        "new_features_launched":    {"type": "array", "items": {"type": "string"}},
        "recent_partnerships_and_integrations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "partner":     {"type": "string"},
                    "type":        {"type": "string",
                                    "enum": ["Integration", "Acquisition",
                                             "Strategic Partnership",
                                             "Distribution", "Investment"]},
                    "description": {"type": "string"},
                    "source":      {"type": "string"},
                    "date":        {"type": "string"},
                },
            },
        },
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string"},
                    "domain": {"type": "string"},
                },
            },
        },
        # ── Phase B fields ────────────────────────────────────────
        "current_problems_struggling_with": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "user_type":   {"type": "string"},
                    "frequency":   {"type": "string",
                                    "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":      {"type": "string"},
                    "date":        {"type": "string"},
                    "effect":      {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "user_complaints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue":     {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string",
                                  "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "differentiators": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature":   {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string",
                                  "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "strategic_moves": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "move":      {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string",
                                  "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "regulatory_and_legal_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue":        {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    "status":       {"type": "string",
                                     "enum": ["Active", "Resolved",
                                              "Under Investigation", "Unknown"]},
                    "source":       {"type": "string"},
                    "date":         {"type": "string"},
                    "effect":       {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "market_sentiment": {
            "type": "object",
            "properties": {
                "overall":              {"type": "string",
                                         "enum": ["Positive", "Neutral",
                                                  "Negative", "Mixed"]},
                "analyst_view":         {"type": "string"},
                "user_community_view":  {"type": "string"},
                "source":               {"type": "string"},
                "date":                 {"type": "string"},
            },
        },
        "other_crucial_details": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["company_name", "domain", "industry_and_segment", "competitors"],
}


# ============================================================
#  JSON HELPERS
# ============================================================

def _fix_json(json_str: str) -> str:
    """Repair common model output issues (trailing commas only)."""
    return re.sub(r",(\s*[}\]])", r"\1", json_str)


def _extract_json(text: str) -> str:
    """
    Robustly extract a JSON object from model response text.

    Handles:
    - Markdown code fences  (```json ... ```)
    - Google Search grounding metadata  {url: ...}  before the real JSON
    - Explanatory text before / after the JSON block
    - Trailing notes / citations after the closing }

    Returns the extracted (and trailing-comma-fixed) JSON string,
    or raises ValueError if no valid JSON object can be found.
    """
    # ── 1. Strip markdown code fences ────────────────────────
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # ── 2. Locate the start of the REAL JSON object ──────────
    # Google Search grounding can inject objects like  {url: http://...}
    # before our JSON.  Our JSON always starts with  {"  or  { "
    match = re.search(r'\{\s*"', text)
    json_start = match.start() if match else text.find("{")

    if json_start == -1:
        raise ValueError("No JSON object found in response (no '{' found)")

    # ── 3. Walk backwards from the end to find a valid closing } ──
    # This skips trailing grounding notes that appear after the JSON.
    json_end = len(text)
    while json_end > json_start:
        json_end = text.rfind("}", json_start, json_end)
        if json_end == -1:
            raise ValueError("No JSON object found in response (no '}' found)")
        candidate = text[json_start: json_end + 1]
        fixed = _fix_json(candidate)
        try:
            json.loads(fixed)
            return fixed          # ✅ valid JSON found
        except json.JSONDecodeError:
            pass
        json_end -= 1             # shrink window and try again

    raise ValueError("Could not locate a valid JSON object in the response")


def _parse_response(text: str) -> Dict[str, Any]:
    """Extract and parse JSON from model response text."""
    return json.loads(_extract_json(text))


# ============================================================
#  RAW RESPONSE SAVER
# ============================================================

def _save_raw(text: str, company: str, model: str, attempt: int,
              folder: str = RAW_FOLDER) -> str:
    """
    Save raw model output to disk so nothing is lost even when parsing fails.
    Returns the file path.
    """
    Path(folder).mkdir(parents=True, exist_ok=True)
    safe_name  = re.sub(r"[^a-zA-Z0-9]", "_", company).lower()
    safe_model = re.sub(r"[^a-zA-Z0-9]", "_", model).lower()
    filename   = f"{safe_name}_{safe_model}_attempt{attempt}.txt"
    filepath   = os.path.join(folder, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    return filepath


# ============================================================
#  AI REPAIR PASS
# ============================================================

REPAIR_PROMPT_TEMPLATE = """
You are a data extraction assistant. Below is PARTIAL or MALFORMED research data
about the company "{company}". It may be truncated, have formatting errors, or be
incomplete JSON.

Your job:
1. Read ALL the data below carefully.
2. Extract every fact you can find.
3. Return ONLY a single valid JSON object following the schema exactly.
4. For any field where data is missing or unclear, use "Unable to verify".
5. Do NOT use Google Search. Do NOT add information beyond what is in the raw data.
6. The very first character of your response must be {{ and the very last must be }}.

--- RAW DATA START ---
{raw_text}
--- RAW DATA END ---

{schema}
"""


def _repair_with_ai(
    raw_text: str,
    company: str,
    client: genai.Client,
) -> Optional[Dict[str, Any]]:
    """
    Second-pass repair: feed the partial/broken raw response back to the model
    (without Google Search) and ask it to restructure into valid JSON.
    Much cheaper and faster than a full research call.
    Returns parsed dict on success, None on failure.
    """
    print(f"   🔧 Attempting AI repair with {REPAIR_MODEL} …", end="", flush=True)

    prompt = REPAIR_PROMPT_TEMPLATE.format(
        company=company,
        raw_text=raw_text[:40000],   # cap to avoid prompt token limits
        schema=JSON_SCHEMA_INSTRUCTION,
    )

    try:
        response = client.models.generate_content(
            model=REPAIR_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                # No google_search here — we only restructure existing data
            ),
        )
        repaired_text = response.text or ""
        if not repaired_text.strip():
            print(" ⚠️  empty repair response")
            return None

        result = _parse_response(repaired_text)
        result["_repair_note"] = (
            "This profile was assembled from a partial raw response via AI repair pass."
        )
        print(" ✅ repair succeeded")
        return result

    except Exception as e:
        print(f" ❌ repair failed: {str(e)[:80]}")
        return None


# ============================================================
#  MAIN CLASS
# ============================================================

class GeminiCompanyResearcher:
    """
    Deep company researcher using Gemini models with Google Search grounding.

    Strategy
    --------
    1. Try MODELS_PRIMARY (gemini-2.5-flash, gemini-2.5-flash-lite) first.
       Schema is embedded in the prompt; response text is parsed manually.

    2. If all primary models fail, fall back to MODELS_STRUCTURED.
       These use response_mime_type="application/json" + response_schema.

    3. For every model, all API keys are tried before moving to the next model.

    4. On JSON parse failure:
       a. Save the raw response to  data/raw/  (never lose data).
       b. Run an AI repair pass: feed the raw text back to a cheap model
          (no Google Search) and ask it to restructure into valid JSON.
       c. If repair also fails, continue to the next model/key.

    Token limit fix
    ---------------
    The previous version used max_output_tokens=8192 which truncated full
    company profiles (15,000-25,000 tokens).  Now set to 65,536.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = TIME_LIMIT,
    ):
        self.api_keys = self._load_api_keys(api_key)
        if not self.api_keys:
            raise ValueError(
                "No API keys found. Set GEMINI_API_KEY (and optionally "
                "GEMINI_API_KEY_2 … GEMINI_API_KEY_14) in your .env file."
            )

        self.timeout = timeout
        self.current_key_index = 0
        self.client = genai.Client(api_key=self.api_keys[0])

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Max output tokens : {MAX_OUTPUT_TOKENS:,}")
        print(f"✅ Primary models  : {', '.join(MODELS_PRIMARY)}")
        print(f"✅ Fallback models : {', '.join(MODELS_STRUCTURED)}\n")

    # ------------------------------------------------------------------
    #  API key helpers
    # ------------------------------------------------------------------

    def _load_api_keys(self, provided: Optional[str]) -> List[str]:
        keys = []
        if provided:
            keys.append(provided)
        for i in range(1, 15):
            env_name = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
            k = os.getenv(env_name)
            if k and k not in keys:
                keys.append(k)
        return keys

    def _switch_key(self) -> bool:
        if self.current_key_index < len(self.api_keys) - 1:
            self.current_key_index += 1
            self.client = genai.Client(api_key=self.api_keys[self.current_key_index])
            print(f"   🔑 Switched to API key #{self.current_key_index + 1}")
            return True
        return False

    def _reset_to_first_key(self):
        self.current_key_index = 0
        self.client = genai.Client(api_key=self.api_keys[0])

    # ------------------------------------------------------------------
    #  Build prompt
    # ------------------------------------------------------------------

    def _build_prompt_phase_a(self, company_query: str, domain: Optional[str]) -> str:
        """Phase A: extract core company facts (small output, fits any token window)."""
        domain_ctx = f" (Official Domain: {domain})" if domain else ""
        return (
            f"Perform research on the company: {company_query}{domain_ctx}.\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Your ENTIRE response must be a single valid JSON object and NOTHING else.\n"
            f"2. Do NOT include any text, explanation, markdown, or code fences.\n"
            f"3. Do NOT include any grounding or citation notes — pure JSON only.\n"
            f"4. The first character must be {{ and the last must be }}.\n"
            f"5. Use Google Search to find LATEST verified sources from 2023-2026.\n"
            f"6. If unverifiable, mark as 'Unable to verify' — NEVER fabricate.\n"
            f"\n{JSON_SCHEMA_PHASE_A}"
        )

    def _build_prompt_phase_b(
        self,
        company_query: str,
        domain: Optional[str],
        phase_a_data: Dict[str, Any],
    ) -> str:
        """
        Phase B: deep analysis fields.
        Phase A results are injected as context so the model understands
        exactly which company/segment/positioning it is analysing.
        """
        domain_ctx = f" (Official Domain: {domain})" if domain else ""

        # Scalar context fields passed from Phase A
        ctx_fields = [
            "company_name", "domain", "industry_and_segment",
            "key_positioning", "revenue_model", "no_of_users",
            "annual_revenue", "funding_raised", "funding_stage",
            "available_platforms", "exact_hq_location", "employee_count",
        ]
        context_lines = "\n".join(
            f"  {k}: {phase_a_data.get(k, 'N/A')}" for k in ctx_fields
        )

        # Array context fields — join as compact comma-separated strings
        competitors_ctx = ", ".join(
            c.get("name", "") for c in phase_a_data.get("competitors", [])
        ) or "N/A"
        segments_ctx  = ", ".join(phase_a_data.get("target_customer_segments", [])) or "N/A"
        pricing_ctx   = " | ".join(phase_a_data.get("pricing_tiers", [])) or "N/A"
        features_ctx  = "; ".join(phase_a_data.get("new_features_launched", [])[:4]) or "N/A"
        partners_ctx  = ", ".join(
            p.get("partner", "") for p in phase_a_data.get("recent_partnerships_and_integrations", [])
        ) or "N/A"

        return (
            f"You are continuing a deep research task on the company: "
            f"{company_query}{domain_ctx}.\n\n"
            f"== COMPANY CONTEXT (already verified in Phase 1) ==\n"
            f"{context_lines}\n"
            f"  main competitors: {competitors_ctx}\n"
            f"  target customer segments: {segments_ctx}\n"
            f"  pricing tiers: {pricing_ctx}\n"
            f"  recent features launched: {features_ctx}\n"
            f"  known partners/integrations: {partners_ctx}\n"
            f"== END CONTEXT ==\n\n"
            f"Using the company context above, now research and extract ONLY the "
            f"deep-analysis fields listed in the schema below.\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Your ENTIRE response must be a single valid JSON object and NOTHING else.\n"
            f"2. Do NOT include any text, explanation, markdown, or code fences.\n"
            f"3. Do NOT include any grounding or citation notes — pure JSON only.\n"
            f"4. The first character must be {{ and the last must be }}.\n"
            f"5. Use Google Search to find LATEST verified sources from 2023-2026.\n"
            f"6. If unverifiable, mark as 'Unable to verify' — NEVER fabricate.\n"
            f"7. Include exact source URLs for every analysis item.\n"
            f"8. Descriptions under 200 characters each.\n"
            f"\n{JSON_SCHEMA_PHASE_B}"
        )

    # ── Legacy single-prompt builder (used only if 2-phase is disabled) ──
    def _build_prompt(self, company_query: str, domain: Optional[str]) -> str:
        domain_ctx = f" (Official Domain: {domain})" if domain else ""
        return (
            f"Perform exhaustive research on the company: {company_query}{domain_ctx}.\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Your ENTIRE response must be a single valid JSON object and NOTHING else.\n"
            f"2. Do NOT include any text, explanation, markdown, or code fences.\n"
            f"3. Do NOT include any grounding or citation notes — pure JSON only.\n"
            f"4. The first character must be {{ and the last must be }}.\n"
            f"5. Use Google Search to find LATEST verified sources from 2023-2026.\n"
            f"6. If unverifiable, mark as 'Unable to verify' — NEVER fabricate.\n"
            f"7. Include exact source URLs for every analysis item.\n"
            f"8. Descriptions under 200 characters each.\n"
            f"\n{JSON_SCHEMA_INSTRUCTION}"
        )

    # ------------------------------------------------------------------
    #  Single API call helpers
    # ------------------------------------------------------------------

    def _call_primary(self, model: str, prompt: str) -> str:
        """
        Primary model call — no native JSON mode.
        Non-streaming first; streaming fallback on failure.
        Uses MAX_OUTPUT_TOKENS to avoid truncation.
        """
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception:
            # Fallback to streaming
            full_text = ""
            start = time.time()
            for chunk in self.client.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=config,
            ):
                if time.time() - start > self.timeout:
                    raise TimeoutError(f"Stream exceeded {self.timeout}s")
                if chunk.text:
                    full_text += chunk.text
            return full_text

    def _call_structured(self, model: str, prompt: str) -> str:
        """
        Fallback model call — native JSON output mode with streaming.
        """
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        full_text = ""
        start = time.time()
        for chunk in self.client.models.generate_content_stream(
            model=model,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            ],
            config=config,
        ):
            if time.time() - start > self.timeout:
                raise TimeoutError(f"Stream exceeded {self.timeout}s")
            if chunk.text:
                full_text += chunk.text
        return full_text

    # ------------------------------------------------------------------
    #  Per-model attempt loop
    # ------------------------------------------------------------------

    def _attempt_model(
        self,
        model: str,
        prompt: str,
        company_query: str,
        use_structured: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Try one model across all API keys.

        On JSON parse failure:
          1. Save raw response to disk (data/raw/).
          2. Run AI repair pass using REPAIR_MODEL (no Google Search).
          3. If repair succeeds → return result.
          4. Otherwise → increment retry counter and continue.

        Returns parsed dict on success, None when this model should be skipped.
        """
        self._reset_to_first_key()
        key_idx    = 0
        attempt_no = 0

        while key_idx < len(self.api_keys):
            json_retries = 0

            while json_retries < MAX_JSON_RETRIES:
                attempt_no += 1
                raw_text = ""

                try:
                    time.sleep(RETRY_DELAY)
                    print(
                        f"   ↳ key #{self.current_key_index + 1}, "
                        f"attempt {json_retries + 1}/{MAX_JSON_RETRIES} … ",
                        end="",
                        flush=True,
                    )

                    raw_text = (
                        self._call_structured(model, prompt)
                        if use_structured
                        else self._call_primary(model, prompt)
                    )

                    if not raw_text or not raw_text.strip():
                        print("⚠️  empty response — skipping model")
                        return None

                    result = _parse_response(raw_text)
                    print("✅")
                    return result

                # ── JSON / extraction failure ──────────────────────
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"❌ Parse error: {e}")

                    if raw_text.strip():
                        # Save raw to disk — data is not lost
                        raw_path = _save_raw(
                            raw_text, company_query, model, attempt_no
                        )
                        print(f"   💾 Raw response saved → {raw_path}")

                        # AI repair pass — restructure without re-fetching
                        repaired = _repair_with_ai(raw_text, company_query, self.client)
                        if repaired is not None:
                            return repaired

                    json_retries += 1
                    if json_retries >= MAX_JSON_RETRIES:
                        print(f"   ✗ {model} — {MAX_JSON_RETRIES} failures, skipping")
                        return None
                    time.sleep(1.5)

                # ── Timeout ───────────────────────────────────────
                except TimeoutError as te:
                    print(f"⏱️  {te} — skipping model")
                    return None

                # ── API / network errors ───────────────────────────
                except Exception as e:
                    msg = str(e).lower()
                    print(f"❌ {str(e)[:120]}")

                    quota_hit   = any(x in msg for x in
                                      ["429", "resource_exhausted", "quota", "rate_limit"])
                    unavailable = any(x in msg for x in
                                      ["503", "unavailable", "high demand",
                                       "404", "not found"])
                    auth_error  = any(x in msg for x in
                                      ["403", "permission", "api_key", "invalid_argument"])

                    if quota_hit:
                        if self._switch_key():
                            key_idx += 1
                            break   # re-enter outer while with new key
                        else:
                            print("   ✗ All API keys exhausted for this model")
                            return None
                    elif unavailable or auth_error:
                        print(f"   ✗ Model '{model}' unavailable/forbidden — skipping")
                        return None
                    else:
                        json_retries += 1
                        if json_retries >= MAX_JSON_RETRIES:
                            print(f"   ✗ {model} — too many errors, skipping")
                            return None
            else:
                # inner while finished without break → move to next key
                key_idx += 1
                if key_idx < len(self.api_keys):
                    if not self._switch_key():
                        break
                continue
            # break from quota → outer while continues

        return None

    # ------------------------------------------------------------------
    #  Public: perform_research
    # ------------------------------------------------------------------

    def _run_phase(
        self,
        prompt: str,
        company_query: str,
        phase_label: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Run one research phase through PRIMARY → STRUCTURED fallback chain.
        Returns the parsed dict on success, or None if every model fails.
        """
        # ── Primary models ──────────────────────────────────────────
        for model in MODELS_PRIMARY:
            print(f"\n🔍 [{phase_label} / PRIMARY] {model}")
            result = self._attempt_model(
                model, prompt, company_query, use_structured=False
            )
            if result is not None:
                print(f"\n✅ {phase_label} success with primary model: {model}")
                return result

        print(f"\n⚠️  [{phase_label}] All primary models failed — trying structured fallbacks …")

        # ── Structured fallback models ──────────────────────────────
        for model in MODELS_STRUCTURED:
            print(f"\n🔍 [{phase_label} / FALLBACK] {model}")
            result = self._attempt_model(
                model, prompt, company_query, use_structured=True
            )
            if result is not None:
                print(f"\n✅ {phase_label} success with fallback model: {model}")
                return result

        return None

    def perform_research(
        self,
        company_query: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Two-phase research strategy:

        Phase A — Core facts (small output, fits small token windows):
            company identity, financials, founders, competitors, etc.

        Phase B — Deep analysis (large output, uses Phase A as context):
            current_problems_struggling_with, differentiators, user_complaints,
            strategic_moves, milestones, new_features_launched, other_crucial_details.

        The two JSON objects are merged into one final result before returning,
        so the caller always receives the complete schema.
        """

        # ── PHASE A: core facts ─────────────────────────────────────
        print("\n" + "=" * 60)
        print("  PHASE A — Core company facts")
        print("=" * 60)

        prompt_a = self._build_prompt_phase_a(company_query, domain)
        phase_a_result = self._run_phase(prompt_a, company_query, "PHASE-A")

        if phase_a_result is None:
            return {
                "error": "Phase A failed — all models and API keys exhausted",
                "company_query": company_query,
            }

        # ── PHASE B: deep analysis (context-aware) ──────────────────
        print("\n" + "=" * 60)
        print("  PHASE B — Deep analysis (problems / differentiators / etc.)")
        print("=" * 60)

        prompt_b = self._build_prompt_phase_b(company_query, domain, phase_a_result)
        phase_b_result = self._run_phase(prompt_b, company_query, "PHASE-B")

        if phase_b_result is None:
            # Phase B failed — return Phase A data with empty analysis fields
            # so the caller still gets a usable (partial) result.
            print(
                "\n⚠️  Phase B failed — returning Phase A data with empty analysis fields."
            )
            phase_a_result.update({
                "current_problems_struggling_with": [],
                "user_complaints": [],
                "differentiators": [],
                "strategic_moves": [],
                "regulatory_and_legal_issues": [],
                "market_sentiment": {},
                "other_crucial_details": [],
                "_phase_b_note": "Phase B (deep analysis) failed — all models exhausted.",
            })
            return phase_a_result

        # ── MERGE: Phase A (core) + Phase B (analysis) → final JSON ─
        # Phase A is the base; Phase B fields are overlaid on top.
        # Any stray top-level keys Phase B may have emitted are ignored.
        PHASE_B_KEYS = {
            "current_problems_struggling_with",
            "user_complaints",
            "differentiators",
            "strategic_moves",
            "regulatory_and_legal_issues",
            "market_sentiment",
            "other_crucial_details",
        }

        merged = dict(phase_a_result)
        for key in PHASE_B_KEYS:
            if key in phase_b_result:
                merged[key] = phase_b_result[key]
            else:
                merged.setdefault(key, [])

        print("\n✅ Both phases complete — results merged.")
        return merged

    # ------------------------------------------------------------------
    #  Save results
    # ------------------------------------------------------------------

    def save_results(
        self,
        data: Dict[str, Any],
        original_query: str,
        storage_folder: str = STORAGE_FOLDER,
    ) -> Optional[str]:
        if "error" in data:
            print(f"⚠️  Skipping save — error in data: {data['error']}")
            return None
        try:
            name       = data.get("company_name", original_query)
            clean_name = re.sub(r"[^a-zA-Z0-9]", "_", name).lower()

            Path(storage_folder).mkdir(parents=True, exist_ok=True)
            file_path = os.path.join(storage_folder, f"{clean_name}.json")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"\n💾 Saved → {file_path}")
            return file_path
        except Exception as e:
            print(f"❌ Save failed: {e}")
            return None


# ============================================================
#  PUBLIC INTERFACE
# ============================================================

def run_research_task(
    company_input: str,
    company_domain: Optional[str] = None,
    storage_folder: str = "data/results",
    timeout: int = TIME_LIMIT,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One-call interface for external scripts / batch pipelines.

    Parameters
    ----------
    company_input  : Company name (required)
    company_domain : Optional official domain, e.g. "stripe.com"
    storage_folder    : Where to write the JSON result file
    timeout        : Per-request streaming timeout in seconds
    api_key        : Single API key override (otherwise reads from .env)
    """


    try:
        researcher = GeminiCompanyResearcher(api_key=api_key, timeout=timeout)
        result     = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input,
                                                storage_folder=storage_folder)
            return {"status": "success", "file": file_path, "data": result}
        else:
            return {"status": "error", "message": result["error"]}

    except Exception as e:
        return {"status": "error", "message": f"Task execution failed: {e}"}


# ============================================================
#  CLI ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        target_company = input("Enter the company name to research: ").strip()
        if not target_company:
            print("❌ Company name cannot be empty!")
            exit(1)

        target_domain = (
            input("Enter company domain (optional, press Enter to skip): ").strip()
            or None
        )

        print("\n" + "=" * 80)
        print("  COMPANY RESEARCH — STARTING")
        print("=" * 80 + "\n")

        outcome = run_research_task(target_company, target_domain)

        if outcome["status"] == "success":
            d = outcome["data"]
            print("\n" + "=" * 80)
            print("  ✅ RESEARCH COMPLETE")
            print("=" * 80)
            print(f"  📁 File      : {outcome['file']}")
            if d.get("_repair_note"):
                print(f"  🔧 Note      : {d['_repair_note']}")
            print(f"  🏢 Company   : {d.get('company_name', 'N/A')}")
            print(f"  🌐 Domain    : {d.get('domain', 'N/A')}")
            print(f"  🔗 LinkedIn  : {d.get('linkedin_company_page', 'N/A')}")
            print(f"  🏭 Industry  : {d.get('industry_and_segment', 'N/A')}")
            print(f"  📅 Founded   : {d.get('year_founded', 'N/A')}")
            print(f"  👤 Employees : {d.get('employee_count', 'N/A')}")
            print(f"  💰 Revenue   : {d.get('annual_revenue', 'N/A')}")
            print(f"  💵 Funding   : {d.get('funding_raised', 'N/A')} ({d.get('funding_stage', 'N/A')})")
            print(f"  👥 Users     : {d.get('no_of_users', 'N/A')}")
            print(f"  🎯 Segments  : {', '.join(d.get('target_customer_segments', [])) or 'N/A'}")
            print(f"  💲 Pricing   : {len(d.get('pricing_tiers', []))} tier(s) found")
            print(f"  🏆 Problems  : {len(d.get('current_problems_struggling_with', []))} items")
            print(f"  ⭐ Differentiators: {len(d.get('differentiators', []))} items")
            print(f"  📣 Complaints: {len(d.get('user_complaints', []))} items")
            print(f"  ♟️  Moves     : {len(d.get('strategic_moves', []))} items")
            print(f"  ⚖️  Legal     : {len(d.get('regulatory_and_legal_issues', []))} items")
            print(f"  🤝 Partnerships: {len(d.get('recent_partnerships_and_integrations', []))} items")
            sentiment = d.get('market_sentiment', {})
            print(f"  📊 Sentiment : {sentiment.get('overall', 'N/A')}")
        else:
            print("\n" + "=" * 80)
            print("  ❌ RESEARCH FAILED")
            print("=" * 80)
            print(f"  Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.")
        exit(1)