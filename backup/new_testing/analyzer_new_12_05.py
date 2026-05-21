"""
analyzer.py - Unified Content Analysis Engine
Standalone callable module for analyzing pre-scraped and live data across all platforms.

Usage:
    from analyzer import analyzer
    result = analyzer(data, mode="detailed", platform="auto")

    python analyzer.py --input data.json --mode detailed
    python analyzer.py --input extracted/ --batch
"""

import os
import json
import time
import argparse
import logging
from typing import List, Union
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("analyzer")

# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER KEYS
# All three are optional — at least one must be set for live calls.
# If all fail, the fallback static result is returned so nothing crashes.
# ─────────────────────────────────────────────────────────────────────────────
HF_TOKEN    = os.environ.get("HF_TOKEN", "")
GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY  = os.environ.get("OPENAI_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK STATIC RESULTS
# Returned when all 3 providers fail — always the same shape as a real result.
# ─────────────────────────────────────────────────────────────────────────────
FALLBACK_RESULTS = {
    "app_store": {
        "summary": "App analysis unavailable — all providers failed.",
        "strengths": ["Could not retrieve data"],
        "weaknesses": ["Could not retrieve data"],
        "sentiment": "Neutral",
        "recommendation": "Retry with a valid API key.",
    },
    "play_store": {
        "summary": "Play Store analysis unavailable — all providers failed.",
        "permissions_risk": "Unknown",
        "strengths": ["Could not retrieve data"],
        "concerns": ["Could not retrieve data"],
    },
    "reddit": {
        "summary": "Reddit analysis unavailable — all providers failed.",
        "sentiment": "Neutral",
        "engagement": "Unknown",
        "controversy": "Unknown",
        "key_topics": ["Could not retrieve data"],
    },
    "youtube": {
        "summary": "YouTube analysis unavailable — all providers failed.",
        "content_quality": "Unknown",
        "engagement": "Unknown",
        "key_insights": ["Could not retrieve data"],
    },
    "generic": {
        "summary": "Analysis unavailable — all providers failed.",
        "key_points": ["Could not retrieve data"],
        "sentiment": "Neutral",
        "recommendations": ["Retry with a valid API key."],
    },
}


class UniversalAnalyzer:

    MODES = {
        "quick":         {"tokens": 500},
        "detailed":      {"tokens": 1000},
        "comprehensive": {"tokens": 2000},
    }

    PLATFORMS = ["app_store", "play_store", "reddit", "youtube", "generic"]

    def __init__(self, mode: str = "detailed"):
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode. Choose: {list(self.MODES.keys())}")
        self.mode   = mode
        self.tokens = self.MODES[mode]["tokens"]

    # ── Public: single item ───────────────────────────────────────────────────

    def analyze(self, data: Union[dict, str], platform: str = "auto", custom_prompt: str = "") -> dict:
        # Load from file path if string
        if isinstance(data, str):
            try:
                with open(data, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                return {"status": "error", "error": f"Failed to load file: {e}"}

        if platform == "auto":
            platform = self._detect_platform(data)
        if platform not in self.PLATFORMS:
            platform = "generic"

        prompt = custom_prompt or self._build_prompt(data, platform)

        # Try all 3 providers in order — return first success
        analysis, provider_used, error_log = self._call_with_fallbacks(prompt)

        if analysis is None:
            # All 3 providers failed — return static fallback so caller never gets None
            log.warning(f"All providers failed: {error_log}. Returning static fallback.")
            return {
                "status":            "fallback",
                "platform":          platform,
                "mode":              self.mode,
                "analysis":          FALLBACK_RESULTS.get(platform, FALLBACK_RESULTS["generic"]),
                "provider_used":     "none",
                "errors":            error_log,
                "timestamp":         datetime.now().isoformat(),
            }

        return {
            "status":        "success",
            "platform":      platform,
            "mode":          self.mode,
            "analysis":      analysis,
            "provider_used": provider_used,
            "timestamp":     datetime.now().isoformat(),
        }

    # ── Public: batch ─────────────────────────────────────────────────────────

    def batch_analyze(self, items: List[Union[dict, str]], platform: str = "auto") -> dict:
        results    = []
        errors     = []

        for i, item in enumerate(items, 1):
            try:
                result = self.analyze(item, platform=platform)
                results.append(result)
                print(f"  [{i}/{len(items)}] {'✓' if result['status'] == 'success' else '~'}")
            except Exception as e:
                errors.append({"index": i, "error": str(e)})
                print(f"  [{i}/{len(items)}] ✗ {e}")

        success_count = sum(1 for r in results if r.get("status") == "success")
        return {
            "batch_mode":     self.mode,
            "platform":       platform,
            "items_analyzed": len(results),
            "items_success":  success_count,
            "items_fallback": len(results) - success_count,
            "items_failed":   len(errors),
            "results":        results,
            "errors":         errors,
            "completed_at":   datetime.now().isoformat(),
        }

    # ── Fallback chain ────────────────────────────────────────────────────────

    def _call_with_fallbacks(self, prompt: str):
        """
        Try HuggingFace → Gemini → OpenAI in order.
        Returns (parsed_analysis, provider_name, error_log).
        analysis is None if all three fail.
        """
        error_log = []

        # ── Provider 1: HuggingFace (Qwen 72B) ───────────────
        if HF_TOKEN:
            try:
                start = time.time()
                from huggingface_hub import InferenceClient
                client = InferenceClient(api_key=HF_TOKEN)
                resp   = client.chat_completion(
                    model="Qwen/Qwen2.5-72B-Instruct",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.tokens,
                    temperature=0.1,
                )
                raw      = resp.choices[0].message.content.strip()
                analysis = self._parse_json(raw)
                log.info(f"HuggingFace succeeded ({int((time.time()-start)*1000)}ms)")
                return analysis, "huggingface/Qwen2.5-72B", error_log
            except Exception as e:
                error_log.append({"provider": "huggingface", "error": str(e)})
                log.warning(f"HuggingFace failed: {e} — trying Gemini")
        else:
            error_log.append({"provider": "huggingface", "error": "HF_TOKEN not set"})

        # ── Provider 2: Gemini ────────────────────────────────
        if GEMINI_KEY:
            try:
                start = time.time()
                from google import genai
                from google.genai import types
                client   = genai.Client(api_key=GEMINI_KEY)
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction="Return ONLY valid JSON. No markdown, no explanation.",
                    ),
                )
                raw      = response.text.strip()
                analysis = self._parse_json(raw)
                log.info(f"Gemini succeeded ({int((time.time()-start)*1000)}ms)")
                return analysis, "gemini/gemini-2.5-flash-lite", error_log
            except Exception as e:
                error_log.append({"provider": "gemini", "error": str(e)})
                log.warning(f"Gemini failed: {e} — trying OpenAI")
        else:
            error_log.append({"provider": "gemini", "error": "GEMINI_API_KEY not set"})

        # ── Provider 3: OpenAI ────────────────────────────────
        if OPENAI_KEY:
            try:
                start = time.time()
                from openai import OpenAI
                client   = OpenAI(api_key=OPENAI_KEY)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Return ONLY valid JSON. No markdown, no explanation."},
                        {"role": "user",   "content": prompt},
                    ],
                    max_tokens=self.tokens,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                raw      = response.choices[0].message.content.strip()
                analysis = self._parse_json(raw)
                log.info(f"OpenAI succeeded ({int((time.time()-start)*1000)}ms)")
                return analysis, "openai/gpt-4o-mini", error_log
            except Exception as e:
                error_log.append({"provider": "openai", "error": str(e)})
                log.warning(f"OpenAI failed: {e}")
        else:
            error_log.append({"provider": "openai", "error": "OPENAI_API_KEY not set"})

        # All 3 failed
        return None, "none", error_log

    # ── Platform detection ────────────────────────────────────────────────────

    def _detect_platform(self, data: dict) -> str:
        if not isinstance(data, dict):
            return "generic"

        ed = data.get("extracted_data", {})
        if ed:
            meta = ed.get("metadata", {})
            if "app_id" in meta or "trackId" in meta:
                return "app_store"
            if "installs" in meta or "permissions" in meta:
                return "play_store"
            if "subreddit" in ed or "posts" in ed:
                return "reddit"
            if "channel" in ed or "videos" in ed:
                return "youtube"

        if any(k in data for k in ["trackName", "averageUserRating"]):
            return "app_store"
        if any(k in data for k in ["installs", "permissions"]):
            return "play_store"
        if any(k in data for k in ["subreddit", "communityName"]):
            return "reddit"
        if any(k in data for k in ["channelName", "viewCount"]):
            return "youtube"

        return "generic"

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(self, data: dict, platform: str) -> str:
        data_str = json.dumps(data, ensure_ascii=False)[:1500]

        prompts = {
            "app_store": f"""Analyze this App Store app. Return JSON with keys:
"summary" (2-3 sentences), "strengths" (list max 5), "weaknesses" (list max 5),
"sentiment" (Positive/Negative/Neutral), "recommendation" (brief text).
Data: {data_str}
Return ONLY valid JSON.""",

            "play_store": f"""Analyze this Play Store app. Return JSON with keys:
"summary", "permissions_risk" (Low/Medium/High), "strengths" (list max 5), "concerns" (list max 5).
Data: {data_str}
Return ONLY valid JSON.""",

            "reddit": f"""Analyze this Reddit data. Return JSON with keys:
"summary" (2-3 sentences), "sentiment", "engagement" (Low/Medium/High),
"controversy" (Low/Medium/High), "key_topics" (list max 5).
Data: {data_str}
Return ONLY valid JSON.""",

            "youtube": f"""Analyze this YouTube data. Return JSON with keys:
"summary", "content_quality" (Low/Medium/High), "engagement" (Low/Medium/High),
"key_insights" (list max 5).
Data: {data_str}
Return ONLY valid JSON.""",

            "generic": f"""Analyze this data. Return JSON with keys:
"summary", "key_points" (list), "sentiment", "recommendations" (list).
Data: {data_str}
Return ONLY valid JSON.""",
        }
        return prompts.get(platform, prompts["generic"])

    # ── JSON parser ───────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> Union[dict, list]:
        text = text.strip()
        # Strip markdown code fences if present
        if "```" in text:
            parts = text.split("```")
            # Take the block after the first fence
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTION — DO NOT MODIFY
# ─────────────────────────────────────────────────────────────────────────────

def analyzer(
    data: Union[dict, str] = None,
    mode: str = "detailed",
    platform: str = "auto",
    batch: bool = False,
    items: List = None,
) -> dict:
    """
    Main analyzer function - can be imported and called from other modules.

    Args:
        data:     Single data item (dict or file path)
        mode:     "quick", "detailed", or "comprehensive"
        platform: "app_store", "play_store", "reddit", "youtube", or "auto"
        batch:    Enable batch mode
        items:    List of items for batch processing

    Returns:
        Analysis result dictionary

    Example:
        from analyzer import analyzer
        result = analyzer({"title": "Test"}, mode="detailed")
    """
    engine = UniversalAnalyzer(mode=mode)

    if batch and items:
        return engine.batch_analyze(items, platform=platform)
    elif data:
        return engine.analyze(data, platform=platform)
    else:
        return {"error": "No data provided"}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyzer - Universal Content Analysis Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyzer.py --input data.json --mode detailed
  python analyzer.py --input extracted/ --batch
  python analyzer.py --input data.json --platform reddit --mode comprehensive
        """,
    )
    parser.add_argument("--input",    help="Input file or directory")
    parser.add_argument("--mode",     choices=["quick", "detailed", "comprehensive"], default="detailed")
    parser.add_argument("--platform", choices=["app_store", "play_store", "reddit", "youtube", "auto"], default="auto")
    parser.add_argument("--batch",    action="store_true")
    parser.add_argument("--output",   help="Output file path")
    args = parser.parse_args()

    if not args.input:
        print("[ERROR] --input is required")
        return

    engine = UniversalAnalyzer(mode=args.mode)

    if os.path.isdir(args.input) and args.batch:
        items = []
        for f in Path(args.input).glob("*.json"):
            with open(f, encoding="utf-8") as fp:
                items.append(json.load(fp))
        result = engine.batch_analyze(items, platform=args.platform)
    else:
        result = engine.analyze(args.input, platform=args.platform)

    output_file = args.output or "analysis_output.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"[SAVED] {output_file}")
    if result.get("status") == "fallback":
        print(f"[WARN]  All providers failed — static fallback was used")
        for err in result.get("errors", []):
            print(f"        {err['provider']}: {err['error']}")
    elif result.get("provider_used"):
        print(f"[OK]    Provider used: {result['provider_used']}")


if __name__ == "__main__":
    main()