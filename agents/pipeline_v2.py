"""
pipeline.py
===========
Full pipeline runner — Agent 1 through Agent 4.

Reads and writes to:  database_mock/{project_name}/db_document.json

Usage:
    python pipeline.py
    python pipeline.py --project Groww --provider gemini
    python pipeline.py --project Groww --start-from agent2
    python pipeline.py --project Groww --only agent3

Callable from other scripts:
    from pipeline import run_pipeline
    run_pipeline("Groww", provider="gemini", start_from="agent2")
"""

import sys
import json
import asyncio
import argparse
import logging
from typing import Optional

try:
    from agents.paths import project_db_path
except ModuleNotFoundError:
    from paths import project_db_path

log = logging.getLogger("pipeline")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

ORDER = ["agent1", "agent2", "agent3", "agent4"]


def _run_agent1(payload: dict) -> str:
    try:
        from agents.agent1_orchestrator import orchestrate_agent_1
    except ModuleNotFoundError:
        from agent1_orchestrator import orchestrate_agent_1
    return asyncio.run(orchestrate_agent_1(payload))


def _run_agent2(project_name: str, provider: str) -> dict:
    try:
        from agents.agent2_insight import run_agent2
    except ModuleNotFoundError:
        from agent2_insight import run_agent2
    return run_agent2(project_name, provider=provider)


def _run_agent3(project_name: str, provider: str) -> dict:
    try:
        from agents.agent3_synthesis import run_agent3
    except ModuleNotFoundError:
        from agent3_synthesis import run_agent3
    return run_agent3(project_name, provider=provider)


def _run_agent4(project_name: str, provider: str) -> dict:
    try:
        from agents.agent4_brief import run_agent4
    except ModuleNotFoundError:
        from agent4_brief import run_agent4
    return run_agent4(project_name, provider=provider)


def run_pipeline(
    project_name  : str,
    provider      : str = "gemini",
    start_from    : str = "agent1",
    only          : Optional[str] = None,
    agent1_payload: Optional[dict] = None,
) -> dict:
    """
    Run the full pipeline or resume from a specific agent.

    Args:
        project_name:    e.g. "Groww"
        provider:        LLM provider for agents 2/3/4
        start_from:      Resume from this agent
        only:            Run only this one agent
        agent1_payload:  Full payload dict for Agent 1

    Returns:
        Final db_document dict.
    """
    log.info("\n" + "=" * 55)
    log.info(f"  Pipeline Start — {project_name}")
    log.info(f"  Provider : {provider}")
    log.info(f"  From     : {only or start_from}")
    log.info("=" * 55)

    agents_to_run = [only] if only else ORDER[ORDER.index(start_from):]

    for agent_name in agents_to_run:
        log.info(f"\n-- Running {agent_name.upper()} --")
        try:
            if agent_name == "agent1":
                if not agent1_payload:
                    raise ValueError("agent1_payload required when running Agent 1")
                _run_agent1(agent1_payload)
            elif agent_name == "agent2":
                _run_agent2(project_name, provider)
            elif agent_name == "agent3":
                _run_agent3(project_name, provider)
            elif agent_name == "agent4":
                _run_agent4(project_name, provider)
            log.info(f"-- {agent_name.upper()} done --")
        except Exception as e:
            log.error(f"Pipeline stopped at {agent_name}: {e}")
            raise

    doc_path = project_db_path(project_name)
    if doc_path.exists():
        with doc_path.open("r", encoding="utf-8") as f:
            final_doc = json.load(f)
        status = final_doc.get("processing_status", {})
        log.info("\n" + "=" * 55)
        log.info(f"  Pipeline Complete — {project_name}")
        log.info(f"  Agent 2 done : {status.get('agent2_insights_extracted')}")
        log.info(f"  Agent 3 done : {status.get('agent3_synthesis_done')}")
        log.info(f"  Agent 4 done : {status.get('agent4_product_brief_done')}")
        log.info("=" * 55)
        return final_doc
    return {"status": "complete", "project": project_name}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Decision Intelligence Pipeline")
    parser.add_argument("--project",    default=None)
    parser.add_argument("--domain",     default=None)
    parser.add_argument("--provider",   default="gemini",
                        choices=["gemini", "gemini_2", "claude", "openai"])
    parser.add_argument("--start-from", default="agent2",
                        choices=["agent1", "agent2", "agent3", "agent4"])
    parser.add_argument("--only",       default=None,
                        choices=["agent1", "agent2", "agent3", "agent4"])
    args = parser.parse_args()

    if not args.project:
        args.project = input("Enter project name: ").strip()
    if not args.project:
        print("Project name is required.")
        sys.exit(1)

    a1_payload = None
    if args.start_from == "agent1" or args.only == "agent1":
        a1_payload = {"project_name": args.project}
        if args.domain:
            a1_payload["domain"] = args.domain

    run_pipeline(
        project_name   = args.project,
        provider       = args.provider,
        start_from     = args.start_from,
        only           = args.only,
        agent1_payload = a1_payload,
    )
