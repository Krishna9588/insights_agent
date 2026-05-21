"""
agent1_orchestrator_v2.py
=========================
Agent 1 — Intelligence Gathering Orchestrator

Runs all scrapers concurrently and saves output to:
    database_mock/{project_name}/db_document.json
    database_mock/{project_name}/raw/            (individual scraper files)

Supports every mode for Reddit and YouTube:

    Reddit modes    : auto | subreddit | user | search | post
    YouTube modes   : video | channel | search

Callable from other scripts:
    from agent1_orchestrator_v2 import orchestrate_agent_1
    import asyncio
    asyncio.run(orchestrate_agent_1(payload))

Full payload example:
    {
        "project_name": "Groww",
        "domain": "groww.in",
        "play_store": {"link_or_id": "com.nextbillion.groww", "reviews_count": 100},
        "app_store":  {"link_or_id": "1434524388",             "reviews_count": 100},

        "reddit": [
            {"input": "r/groww",       "mode": "subreddit", "limit": 20, "category": "top"},
            {"input": "Groww app",     "mode": "search",    "limit": 15},
            {"input": "u/GrowwWealth", "mode": "user",      "limit": 10}
        ],

        "youtube": [
            {"mode": "search",  "query": "Groww app review",                    "count": 5},
            {"mode": "channel", "channel_url": "https://www.youtube.com/@Groww", "count": 5},
            {"mode": "video",   "video_url": "https://www.youtube.com/watch?v=XXXXX"}
        ],

        "transcripts": {"input_path": "path/to/transcripts/"}
    }

Minimal payload (only mandatory field):
    {"project_name": "Groww"}
"""

import os
import sys
import json
import asyncio
import shutil
import dataclasses
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

log = logging.getLogger("agent1_orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DB_FOLDER = "database_mock"

# Folders that scrapers write to by default (we consolidate them into raw/)
HARDCODED_SCRAPER_FOLDERS = ["reddit_data", "youtube_data", "signals"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_json_serializable(obj):
    """Recursively convert dataclasses → dicts so everything can be JSON-saved."""
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    elif isinstance(obj, list):
        return [make_json_serializable(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    return obj


async def run_scraper_safe(scraper_func, *args, **kwargs) -> Any:
    """
    Wraps any synchronous scraper in an async thread.
    Never crashes the pipeline — returns an error dict on failure.
    """
    try:
        return await asyncio.to_thread(scraper_func, *args, **kwargs)
    except Exception as e:
        log.error(f"[{scraper_func.__name__}] failed: {e}")
        return {"status": "error", "error": str(e)}


def _consolidate_scraper_files(raw_dir: str):
    """
    Move any files that scrapers dropped in hardcoded root folders
    (reddit_data/, youtube_data/, signals/) into raw_dir.
    """
    for folder in HARDCODED_SCRAPER_FOLDERS:
        if os.path.exists(folder):
            try:
                for filename in os.listdir(folder):
                    src  = os.path.join(folder, filename)
                    dest = os.path.join(raw_dir, filename)
                    shutil.move(src, dest)
                os.rmdir(folder)
                log.info(f"Consolidated {folder}/ into raw/")
            except Exception as e:
                log.warning(f"Could not consolidate {folder}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# REDDIT TASK BUILDER
#
# Supports a list of reddit configs so you can scrape multiple
# subreddits / queries / users in one run.
#
# Each config dict:
#   input    (str)  required  — r/name | u/name | search term | post URL
#   mode     (str)  optional  — "subreddit"|"user"|"search"|"post"|None(auto)
#   limit    (int)  optional  — default 10
#   category (str)  optional  — "hot"|"top"|"new"|"rising" (subreddit only)
#   time_filter (str) optional — "week"|"month"|"year"|"all"
#   scrape_comments (bool) optional — default True
# ─────────────────────────────────────────────────────────────────────────────

def _build_reddit_tasks(reddit_configs: List[Dict]) -> Dict[str, Any]:
    """
    Returns a dict of {task_key: coroutine} for all reddit configs.
    Keys are like "reddit_0", "reddit_1" etc.
    """
    try:
        from agent_1.reddit_6_working_f import reddit as reddit_scraper
    except ImportError:
        log.warning("reddit_6_working_f.py not found — skipping all Reddit tasks")
        return {}

    tasks = {}
    for idx, cfg in enumerate(reddit_configs):
        user_input = cfg.get("input", "").strip()
        if not user_input:
            log.warning(f"Reddit config #{idx} has no 'input' field — skipping")
            continue

        task_key = f"reddit_{idx}"
        label    = cfg.get("label", user_input[:30])

        log.info(f"Reddit task [{task_key}]: input={user_input!r} mode={cfg.get('mode','auto')}")

        tasks[task_key] = run_scraper_safe(
            reddit_scraper,
            user_input,
            mode            = cfg.get("mode"),          # None = auto-detect
            limit           = cfg.get("limit", 10),
            category        = cfg.get("category", "hot"),
            time_filter     = cfg.get("time_filter", "week"),
            scrape_comments = cfg.get("scrape_comments", True),
            verbose         = False,
            save            = True,
        )

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE TASK BUILDER
#
# Supports a list of youtube configs — multiple modes in one run.
#
# Each config dict (mode is required):
#
#   mode="video"   → also needs: video_url (str)
#   mode="channel" → also needs: channel_url (str)
#                    optional:   count (int, default 5)
#   mode="search"  → also needs: query (str)
#                    optional:   count (int, default 5)
# ─────────────────────────────────────────────────────────────────────────────

def _build_youtube_tasks(youtube_configs: List[Dict]) -> Dict[str, Any]:
    """
    Returns a dict of {task_key: coroutine} for all youtube configs.
    """
    try:
        from agent_1.youtube_scraper import youtube_scraper as yt_scraper
    except ImportError:
        log.warning("youtube_scraper.py not found — skipping all YouTube tasks")
        return {}

    tasks = {}
    for idx, cfg in enumerate(youtube_configs):
        mode = (cfg.get("mode") or "").strip().lower()
        if not mode:
            log.warning(f"YouTube config #{idx} missing 'mode' — skipping")
            continue

        task_key = f"youtube_{idx}"

        if mode == "video":
            video_url = cfg.get("video_url", "").strip()
            if not video_url:
                log.warning(f"YouTube config #{idx}: mode=video requires 'video_url' — skipping")
                continue
            log.info(f"YouTube task [{task_key}]: mode=video url={video_url[:60]}")
            tasks[task_key] = run_scraper_safe(
                yt_scraper,
                mode      = "video",
                video_url = video_url,
            )

        elif mode == "channel":
            channel_url = cfg.get("channel_url", "").strip()
            if not channel_url:
                log.warning(f"YouTube config #{idx}: mode=channel requires 'channel_url' — skipping")
                continue
            count = cfg.get("count", 5)
            log.info(f"YouTube task [{task_key}]: mode=channel url={channel_url[:60]} count={count}")
            tasks[task_key] = run_scraper_safe(
                yt_scraper,
                mode        = "channel",
                channel_url = channel_url,
                count       = count,
            )

        elif mode == "search":
            query = cfg.get("query", "").strip()
            if not query:
                log.warning(f"YouTube config #{idx}: mode=search requires 'query' — skipping")
                continue
            count = cfg.get("count", 5)
            log.info(f"YouTube task [{task_key}]: mode=search query={query!r} count={count}")
            tasks[task_key] = run_scraper_safe(
                yt_scraper,
                mode  = "search",
                query = query,
                count = count,
            )

        else:
            log.warning(f"YouTube config #{idx}: unknown mode={mode!r} — use video|channel|search")

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

async def orchestrate_agent_1(payload: Dict[str, Any]) -> str:
    """
    Main entry point. Accepts a payload dict, runs all scrapers concurrently,
    saves everything to database_mock/{project_name}/db_document.json.

    Args:
        payload: See module docstring for full schema.

    Returns:
        Filepath of saved db_document.json
    """
    project_name = (payload.get("project_name") or "").strip()
    if not project_name:
        raise ValueError("'project_name' is required in the payload.")

    log.info(f"\n{'='*55}")
    log.info(f"  Agent 1 — {project_name}")
    log.info(f"{'='*55}")

    # Setup directories
    project_dir = os.path.join(DB_FOLDER, project_name)
    raw_dir     = os.path.join(project_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    domain     = (payload.get("domain") or "").strip() or None
    task_map   = {}

    # ── Company Profile (always runs) ────────────────────────────
    try:
        # from agent_1.company_profile_researcher_fix_v2 import run_research_task
        from agent_1.company_profile_double_agent_v3 import run_research_task
        log.info("Task: company_profile")
        task_map["company_profile"] = run_scraper_safe(
            run_research_task,
            company_input  = project_name,
            company_domain = domain,
            storage_folder = raw_dir,
        )
    except ImportError:
        log.warning("company_profile_best.py not found — skipping company profile")

    # ── Play Store ───────────────────────────────────────────────
    if "play_store" in payload:
        try:
            from agent_1.play_store_2_working import play_store
            ps = payload["play_store"]
            log.info(f"Task: play_store  id={ps.get('link_or_id')}")
            task_map["play_store"] = run_scraper_safe(
                play_store,
                input_str   = ps.get("link_or_id"),
                reviews     = ps.get("reviews_count", 100),
                output      = raw_dir,
                interactive = False,
                verbose     = False,
            )
        except ImportError:
            log.warning("play_store_2_working.py not found — skipping Play Store")

    # ── App Store ────────────────────────────────────────────────
    if "app_store" in payload:
        try:
            from agent_1.app_store_3_working import app_store
            ap = payload["app_store"]
            log.info(f"Task: app_store  id={ap.get('link_or_id')}")
            task_map["app_store"] = run_scraper_safe(
                app_store,
                input_str   = ap.get("link_or_id"),
                reviews     = ap.get("reviews_count", 100),
                output      = raw_dir,
                interactive = False,
                verbose     = False,
            )
        except ImportError:
            log.warning("app_store_3_working.py not found — skipping App Store")

    # ── Reddit (supports multiple configs) ──────────────────────
    if "reddit" in payload:
        reddit_cfg = payload["reddit"]

        # Accept both a single dict and a list of dicts
        if isinstance(reddit_cfg, dict):
            reddit_cfg = [reddit_cfg]

        reddit_tasks = _build_reddit_tasks(reddit_cfg)
        task_map.update(reddit_tasks)

    # ── YouTube (supports multiple configs) ─────────────────────
    if "youtube" in payload:
        youtube_cfg = payload["youtube"]

        # Accept both a single dict and a list of dicts
        if isinstance(youtube_cfg, dict):
            youtube_cfg = [youtube_cfg]

        youtube_tasks = _build_youtube_tasks(youtube_cfg)
        task_map.update(youtube_tasks)

    # ── Internal Transcripts ─────────────────────────────────────
    if "transcripts" in payload:
        ts_data    = payload["transcripts"]
        input_path = (ts_data.get("input_path") or "").strip()
        if input_path and os.path.exists(input_path):
            try:
                from agent_1.agent1_internal_cloud import agent1_internal_batch, agent1_internal
                if os.path.isdir(input_path):
                    log.info(f"Task: internal_transcripts (batch) path={input_path}")
                    task_map["internal_transcripts"] = run_scraper_safe(
                        agent1_internal_batch,
                        input_dir  = input_path,
                        output_dir = raw_dir,
                    )
                else:
                    log.info(f"Task: internal_transcripts (single) path={input_path}")
                    task_map["internal_transcripts"] = run_scraper_safe(
                        agent1_internal,
                        input_path = input_path,
                        output_dir = raw_dir,
                    )
            except ImportError:
                log.warning("agent1_internal_cloud.py not found — skipping transcripts")
        else:
            log.warning(f"Transcript path not found or empty: {input_path!r}")

    # ── Run all tasks concurrently ────────────────────────────────
    if not task_map:
        log.warning("No tasks were queued. Check payload keys and scraper imports.")
    else:
        log.info(f"\nDispatching {len(task_map)} task(s): {list(task_map.keys())}")

    keys         = list(task_map.keys())
    results_list = await asyncio.gather(*task_map.values())

    # ── Consolidate loose files written to root ───────────────────
    _consolidate_scraper_files(raw_dir)

    # ── Build final document ──────────────────────────────────────
    scraped_data = make_json_serializable(dict(zip(keys, results_list)))

    # Merge multiple reddit/youtube results under unified keys
    # so Agent 2 has predictable keys to read
    merged_reddit  = {}
    merged_youtube = {}
    clean_data     = {}

    for k, v in scraped_data.items():
        if k.startswith("reddit_"):
            merged_reddit[k] = v
        elif k.startswith("youtube_"):
            merged_youtube[k] = v
        else:
            clean_data[k] = v

    if merged_reddit:
        clean_data["reddit"]  = merged_reddit
    if merged_youtube:
        clean_data["youtube"] = merged_youtube

    final_document = {
        "project_name"     : project_name,
        "domain"           : domain,
        "ingestion_date"   : datetime.now().isoformat(),
        "data_sources"     : clean_data,
        "processing_status": {
            "agent2_insights_extracted" : False,
            "agent3_synthesis_done"     : False,
            "agent4_product_brief_done" : False,
        },
        "agent2_output": {},
        "agent3_output": {},
        "agent4_output": {},
    }

    db_filepath = os.path.join(project_dir, "db_document.json")
    with open(db_filepath, "w", encoding="utf-8") as f:
        json.dump(final_document, f, indent=4, ensure_ascii=False)

    log.info(f"\n{'='*55}")
    log.info(f"  Agent 1 complete")
    log.info(f"  db_document : {db_filepath}")
    log.info(f"  raw files   : {raw_dir}")
    log.info(f"  tasks ran   : {len(keys)}")
    log.info(f"{'='*55}\n")

    return db_filepath


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC HELPER — build a payload interactively (CLI)
# ─────────────────────────────────────────────────────────────────────────────

def build_payload_interactive() -> Dict:
    """
    Interactive CLI to build a full payload.
    Guides the user through every option including multiple reddit/youtube targets.
    """
    print("=" * 55)
    print("  AGENT 1: INTELLIGENCE GATHERING")
    print("=" * 55)

    # Mandatory
    project_name = input("\nProject / Company Name (required): ").strip()
    while not project_name:
        project_name = input("  Cannot be empty. Enter name: ").strip()

    payload: Dict[str, Any] = {"project_name": project_name}

    # Domain
    domain = input(f"Domain for {project_name} (optional, press Enter to skip): ").strip()
    if domain:
        payload["domain"] = domain

    # Play Store
    ps_id = input("Play Store App ID or link (optional): ").strip()
    if ps_id:
        try:
            n = int(input("  How many reviews? [default 100]: ").strip() or "100")
        except ValueError:
            n = 100
        payload["play_store"] = {"link_or_id": ps_id, "reviews_count": n}

    # App Store
    as_id = input("App Store App ID or link (optional): ").strip()
    if as_id:
        try:
            n = int(input("  How many reviews? [default 100]: ").strip() or "100")
        except ValueError:
            n = 100
        payload["app_store"] = {"link_or_id": as_id, "reviews_count": n}

    # Reddit — multiple targets
    reddit_configs = []
    print("\nReddit (you can add multiple targets).")
    print("  Input can be: r/name  |  u/name  |  search phrase  |  post URL")
    print("  Modes       : auto (default) | subreddit | user | search | post")
    while True:
        rd_input = input("  Reddit input (or press Enter to skip/finish): ").strip()
        if not rd_input:
            break
        rd_mode  = input("  Mode [auto]: ").strip() or None
        try:
            rd_limit = int(input("  Limit [10]: ").strip() or "10")
        except ValueError:
            rd_limit = 10
        rd_cat   = input("  Category for subreddit [hot]: ").strip() or "hot"
        cfg = {"input": rd_input, "limit": rd_limit, "category": rd_cat}
        if rd_mode:
            cfg["mode"] = rd_mode
        reddit_configs.append(cfg)
        another = input("  Add another Reddit target? [y/N]: ").strip().lower()
        if another != "y":
            break
    if reddit_configs:
        payload["reddit"] = reddit_configs

    # YouTube — multiple targets
    youtube_configs = []
    print("\nYouTube (you can add multiple targets).")
    print("  Modes: search | channel | video")
    while True:
        yt_mode = input("  YouTube mode (or press Enter to skip/finish): ").strip().lower()
        if not yt_mode:
            break
        if yt_mode == "video":
            url = input("  Video URL: ").strip()
            youtube_configs.append({"mode": "video", "video_url": url})
        elif yt_mode == "channel":
            url = input("  Channel URL: ").strip()
            try:
                count = int(input("  Number of videos [5]: ").strip() or "5")
            except ValueError:
                count = 5
            youtube_configs.append({"mode": "channel", "channel_url": url, "count": count})
        elif yt_mode == "search":
            query = input("  Search query: ").strip()
            try:
                count = int(input("  Number of results [5]: ").strip() or "5")
            except ValueError:
                count = 5
            youtube_configs.append({"mode": "search", "query": query, "count": count})
        else:
            print("  Unknown mode. Use: search | channel | video")
            continue
        another = input("  Add another YouTube target? [y/N]: ").strip().lower()
        if another != "y":
            break
    if youtube_configs:
        payload["youtube"] = youtube_configs

    # Internal transcripts
    ts_path = input("\nPath to internal transcripts folder or file (optional): ").strip()
    if ts_path:
        payload["transcripts"] = {"input_path": ts_path}

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    payload = build_payload_interactive()

    print("\nPayload to be used:")
    print(json.dumps(payload, indent=2))
    confirm = input("\nRun Agent 1 with this payload? [Y/n]: ").strip().lower()
    if confirm in ("", "y"):
        asyncio.run(orchestrate_agent_1(payload))
    else:
        print("Cancelled.")