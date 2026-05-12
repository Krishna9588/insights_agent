"""
analyzer.py - Unified Content Analysis Engine
Standalone callable module for analyzing pre-scraped and live data across all platforms.

Usage:
    from analyzer import analyzer
    result = analyzer(data, mode="detailed", platform="auto")

    python analyzer.py --input data.json --mode detailed
    python analyzer.py --bulk extracted/ --batch
"""

import os
import sys
import json
import time
import argparse
from typing import Optional, Dict, List, Union
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not HF_TOKEN:
    print("[ERROR] HF_TOKEN not found in environment variables")
    sys.exit(1)


class UniversalAnalyzer:
    """Universal analyzer supporting all platforms."""

    MODES = {
        "quick": {"tokens": 500, "depth": "brief"},
        "detailed": {"tokens": 1000, "depth": "comprehensive"},
        "comprehensive": {"tokens": 2000, "depth": "deep analysis"},
    }

    PLATFORMS = ["app_store", "play_store", "reddit", "youtube", "generic"]

    def __init__(self, mode: str = "detailed"):
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode. Choose: {list(self.MODES.keys())}")
        self.mode = mode
        self.config = self.MODES[mode]

    def analyze(self, data: Union[dict, str], platform: str = "auto", custom_prompt: str = "") -> dict:
        """
        Analyze data with auto-detection or specified platform.

        Args:
            data: Dictionary or file path to analyze
            platform: Platform type or "auto" for detection
            custom_prompt: Override default prompt

        Returns:
            Analysis result with status and analysis data
        """
        # Load data if string (filepath)
        if isinstance(data, str):
            try:
                with open(data, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                return {"status": "error", "error": f"Failed to load file: {e}"}

        # Detect platform if auto
        if platform == "auto":
            platform = self._detect_platform(data)

        if platform not in self.PLATFORMS:
            platform = "generic"

        # Build prompt
        if not custom_prompt:
            custom_prompt = self._build_prompt(data, platform)

        # Call HF API
        try:
            from huggingface_hub import InferenceClient

            client = InferenceClient(api_key=HF_TOKEN)
            start = time.time()

            resp = client.chat_completion(
                model="Qwen/Qwen2.5-72B-Instruct",
                messages=[{"role": "user", "content": custom_prompt}],
                max_tokens=self.config["tokens"],
                temperature=0.1,
            )

            elapsed = (time.time() - start) * 1000
            raw = resp.choices[0].message.content.strip()
            analysis = self._parse_json(raw)

            return {
                "status": "success",
                "platform": platform,
                "mode": self.mode,
                "analysis": analysis,
                "processing_time_ms": int(elapsed),
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def batch_analyze(self, items: List[Union[dict, str]], platform: str = "auto") -> dict:
        """Batch analyze multiple items."""
        results = []
        errors = []
        total_time = 0

        print(f"\n[ANALYZER] Batch analyzing {len(items)} items (mode: {self.mode})...")

        for i, item in enumerate(items, 1):
            try:
                result = self.analyze(item, platform=platform)
                results.append(result)
                total_time += result.get("processing_time_ms", 0)

                status = "✓" if result["status"] == "success" else "✗"
                print(f"  [{i}/{len(items)}] {status}")

            except Exception as e:
                errors.append({"index": i, "error": str(e)})
                print(f"  [{i}/{len(items)}] ✗ Error: {e}")

        return {
            "batch_mode": self.mode,
            "platform": platform,
            "items_analyzed": len(results),
            "items_failed": len(errors),
            "success_rate": (len(results) - len(errors)) / len(items) * 100 if items else 0,
            "total_time_ms": int(total_time),
            "results": results,
            "errors": errors,
            "completed_at": datetime.now().isoformat(),
        }

    def _detect_platform(self, data: dict) -> str:
        """Auto-detect platform from data structure."""
        if isinstance(data, dict):
            # Check extracted_data structure
            if "extracted_data" in data:
                ed = data.get("extracted_data", {})
                if "metadata" in ed:
                    meta = ed["metadata"]
                    if "app_id" in meta or "trackId" in meta:
                        return "app_store"
                    elif "installs" in meta or "permissions" in meta:
                        return "play_store"
                elif "subreddit" in ed or "posts" in ed:
                    return "reddit"
                elif "channel" in ed or "videos" in ed:
                    return "youtube"

            # Check direct fields
            if any(k in data for k in ["trackName", "averageUserRating"]):
                return "app_store"
            elif any(k in data for k in ["installs", "permissions"]):
                return "play_store"
            elif any(k in data for k in ["subreddit", "communityName"]):
                return "reddit"
            elif any(k in data for k in ["channelName", "viewCount"]):
                return "youtube"

        return "generic"

    def _build_prompt(self, data: dict, platform: str) -> str:
        """Build analysis prompt based on platform."""
        data_str = json.dumps(data, ensure_ascii=False)[:2000]

        if platform == "app_store":
            return f"""Analyze this Apple App Store app. Return JSON:
- "summary": 2-3 sentence overview
- "strengths": list (max 5)
- "weaknesses": list (max 5)
- "sentiment": "Positive"/"Negative"/"Neutral"
- "recommendation": brief text

Data: {data_str[:1500]}
Return ONLY valid JSON."""

        elif platform == "play_store":
            return f"""Analyze this Google Play Store app. Return JSON:
- "summary": overview
- "permissions_risk": "Low"/"Medium"/"High"
- "strengths": list (max 5)
- "concerns": list (max 5)

Data: {data_str[:1500]}
Return ONLY valid JSON."""

        elif platform == "reddit":
            return f"""Analyze this Reddit post/subreddit. Return JSON:
- "summary": 2-3 sentences
- "sentiment": sentiment analysis
- "engagement": "Low"/"Medium"/"High"
- "controversy": "Low"/"Medium"/"High"
- "key_topics": list (max 5)

Data: {data_str[:1500]}
Return ONLY valid JSON."""

        elif platform == "youtube":
            return f"""Analyze this YouTube video/channel. Return JSON:
- "summary": overview
- "content_quality": "Low"/"Medium"/"High"
- "engagement": "Low"/"Medium"/"High"
- "key_insights": list (max 5)

Data: {data_str[:1500]}
Return ONLY valid JSON."""

        else:
            return f"""Analyze this data. Return JSON with:
- "summary": overview
- "key_points": list
- "sentiment": overall sentiment
- "recommendations": list

Data: {data_str[:1500]}
Return ONLY valid JSON."""

    def _parse_json(self, text: str) -> Union[dict, list]:
        """Extract JSON from response."""
        try:
            text = text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except:
            return {"raw": text}


def analyzer(data: Union[dict, str] = None, mode: str = "detailed", platform: str = "auto", batch: bool = False, items: List = None) -> dict:
    """
    Main analyzer function - can be imported and called from other modules.

    Args:
        data: Single data item (dict or file path)
        mode: "quick", "detailed", or "comprehensive"
        platform: "app_store", "play_store", "reddit", "youtube", or "auto"
        batch: Enable batch mode
        items: List of items for batch processing

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


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Analyzer - Universal Content Analysis Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyzer.py --input data.json --mode detailed
  python analyzer.py --input extracted/ --batch
  python analyzer.py --input data.json --platform reddit --mode comprehensive
        """
    )

    parser.add_argument("--input", help="Input file or directory")
    parser.add_argument("--mode", choices=["quick", "detailed", "comprehensive"], default="detailed")
    parser.add_argument("--platform", choices=["app_store", "play_store", "reddit", "youtube", "auto"], default="auto")
    parser.add_argument("--batch", action="store_true", help="Batch process directory")
    parser.add_argument("--output", help="Output file")

    args = parser.parse_args()

    if not args.input:
        print("[ERROR] --input is required")
        return

    engine = UniversalAnalyzer(mode=args.mode)

    # Load data
    if os.path.isdir(args.input) and args.batch:
        items = []
        for f in Path(args.input).glob("*.json"):
            with open(f) as fp:
                items.append(json.load(fp))
        result = engine.batch_analyze(items, platform=args.platform)
    else:
        result = engine.analyze(args.input, platform=args.platform)

    # Output
    output_file = args.output or "analysis_output.json"
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"[SAVED] {output_file}")


if __name__ == "__main__":
    main()