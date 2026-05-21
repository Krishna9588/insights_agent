# """
# pipeline.py
# ===========
# Full Pipeline Runner — runs Agent 1 → 2 → 3 → 4 in sequence.
#
# Usage:
#   python pipeline.py --project Groww --domain groww.in
#   python pipeline.py --project Groww --start-from agent2   # resume from agent2
#   python pipeline.py --project Groww --only agent4         # run just one agent
# """
#
# from __future__ import annotations
#
# import argparse
# import json
# import logging
# import sys
# from pathlib import Path
#
# logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
# log = logging.getLogger("pipeline")
#
# from agents.agent1_orchestrator import run_agent1, _make_project_id
# from agents.agent2_insight import run_agent2
# from agents.agent3_synthesis import run_agent3
# from agents.agent4_brief import run_agent4
#
#
# AGENTS = {
#     "agent1": run_agent1,
#     "agent2": run_agent2,
#     "agent3": run_agent3,
#     "agent4": run_agent4,
# }
#
# ORDER = ["agent1", "agent2", "agent3", "agent4"]
#
#
# def run_pipeline(
#     project_name   : str,
#     domain         : str | None   = None,
#     internal_files : list | None  = None,
#     start_from     : str          = "agent1",
#     only           : str | None   = None,
#     skip_steps     : list | None  = None,
# ) -> dict:
#     """
#     Run the full decision intelligence pipeline.
#
#     Args:
#         project_name:   e.g. "Groww"
#         domain:         e.g. "groww.in"
#         internal_files: list of transcript file paths for Agent 1C
#         start_from:     resume from this agent ("agent1" | "agent2" | "agent3" | "agent4")
#         only:           run only this agent
#         skip_steps:     Agent 1 steps to skip (["A", "B", "C"])
#
#     Returns:
#         Final project document.
#     """
#     project_id = _make_project_id(project_name)
# agents.
#     log.info(f"\n{'='*60}")
#     log.info(f"  🚀 Decision Intelligence Pipeline")
#     log.info(f"  Project : {project_name}  ({project_id})")
#     log.info(f"  Domain  : {domain or 'not set'}")
#     log.info(f"{'='*60}\n")
#
#     agents_to_run = [only] if only else ORDER[ORDER.index(start_from):]
#
#     doc = None
#     for agent_name in agents_to_run:
#         log.info(f"\n── Running {agent_name.upper()} ────────────────────────────")
#
#         if agent_name == "agent1":
#             doc = run_agent1(
#                 project_name=project_name,
#                 domain=domain,
#                 internal_files=internal_files,
#                 skip_steps=skip_steps,
#             )
#         else:
#             fn  = AGENTS[agent_name]
#             doc = fn(project_id)
#
#         status = doc.get("status", "unknown")
#         log.info(f"── {agent_name.upper()} done. Status: {status} ──")
#
#         if "error" in status:
#             log.error(f"Pipeline stopped at {agent_name} due to error.")
#             break
#
#     log.info(f"\n{'='*60}")
#     log.info(f"  ✅ Pipeline complete. Final status: {doc.get('status')}")
#     log.info(f"{'='*60}\n")
#
#     return doc
#
#
# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Decision Intelligence Pipeline")
#     parser.add_argument("--project",    required=True,  help="Company name, e.g. Groww")
#     parser.add_argument("--domain",     default=None,   help="e.g. groww.in")
#     parser.add_argument("--internal",   nargs="*",      help="Internal transcript files")
#     parser.add_argument("--start-from", default="agent1",
#                         choices=["agent1","agent2","agent3","agent4"])
#     parser.add_argument("--only",       default=None,
#                         choices=["agent1","agent2","agent3","agent4"])
#     parser.add_argument("--skip",       nargs="*",      help="Agent1 steps to skip: A B C")
#     parser.add_argument("--output",     default=None,   help="Save final doc here")
#
#     args = parser.parse_args()
#
#     result = run_pipeline(
#         project_name   = args.project,
#         domain         = args.domain,
#         internal_files = args.internal,
#         start_from     = args.start_from,
#         only           = args.only,
#         skip_steps     = args.skip,
#     )
#
#     if args.output:
#         Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False))
#         log.info(f"Output saved to: {args.output}")
#     else:
#         # Print just the summary
#         summary = {
#             "project_id"  : result.get("project_id"),
#             "status"      : result.get("status"),
#             "problems"    : result.get("agent2", {}).get("summary"),
#             "insights"    : result.get("agent3", {}).get("summary"),
#             "briefs"      : result.get("agent4", {}).get("summary"),
#             "sprint_focus": result.get("agent4", {}).get("sprint_focus"),
#         }
#         print(json.dumps(summary, indent=2))