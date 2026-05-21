from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.agent1_internal_cloud import agent1_internal_batch
from agents.agent1_orchestrator import make_json_serializable
from agents.agent2_insight import _extract_signals


def request_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def check(label: str, fn) -> bool:
    try:
        result = fn()
        print(f"[OK] {label}: {result}")
        return True
    except Exception as exc:
        print(f"[FAIL] {label}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Insights backend and local transcript ingestion.")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--transcripts", default=str(ROOT / "transcript_input"))
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()

    passed = 0
    total = 0

    def run(label: str, fn) -> None:
        nonlocal passed, total
        total += 1
        if check(label, fn):
            passed += 1

    if not args.skip_api:
        run("API health", lambda: request_json(args.api, "/health")["status"])
        run("List projects", lambda: len(request_json(args.api, "/projects").get("projects", [])))
        run("List jobs", lambda: len(request_json(args.api, "/jobs").get("jobs", [])))
        run(
            "Create news monitor config",
            lambda: request_json(args.api, "/news/monitors", {
                "name": "Smoke Test Monitor",
                "query": "Track SEBI and RBI fintech updates for Indian wealthtech apps.",
                "schedule_time": "20:00",
                "timezone": "Asia/Kolkata",
                "sources": ["news", "sebi", "rbi"],
                "enabled": True,
            })["id"],
        )

    transcript_dir = Path(args.transcripts)
    run("Transcript folder exists", lambda: transcript_dir.exists())

    def transcript_batch() -> str:
        results = agent1_internal_batch(str(transcript_dir), "/private/tmp/insights_smoke_signals")
        signals = _extract_signals({"internal_transcripts": make_json_serializable(results)})
        count = len(signals.get("internal_signals", []))
        if count == 0:
            raise AssertionError("No transcript signals extracted")
        return f"{len(results)} file(s), {count} downstream signal(s)"

    run("Local transcript batch extraction", transcript_batch)

    print(f"\nSmoke result: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
