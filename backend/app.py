from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.agent5_copilot import ask_copilot
from agents.path import DB_ROOT, STATE_ROOT, project_db_path, ensure_all_dirs
from agents.pipeline_v2 import run_pipeline

# Import new services
from core.database import get_db, ChatMessage, ChatSession
from core.copilot_service import create_copilot
from core.drive_config import get_drive_config
from core.summary_generator import summarize_daily_chats


app = FastAPI(title="Insights Platform API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_PATH = STATE_ROOT / "jobs.json"
JOBS_LOCK = threading.Lock()
NEWS_MONITORS_PATH = STATE_ROOT / "news_monitors.json"
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class PipelineRunRequest(BaseModel):
    project_name: str
    provider: str = "gemini"
    domain: Optional[str] = None
    start_from: str = "agent1"
    only: Optional[str] = None
    agent1_payload: Optional[Dict[str, Any]] = None


class LocalTranscriptRequest(BaseModel):
    project_name: str
    input_path: str
    provider: str = "gemini"
    domain: Optional[str] = None
    run_async: bool = True


class GoogleDriveRequest(BaseModel):
    project_name: str
    folder_id: str
    provider: str = "gemini"
    domain: Optional[str] = None
    credentials_path: Optional[str] = None
    token_path: Optional[str] = None
    include_existing: bool = False
    run_async: bool = True


class CopilotRequest(BaseModel):
    project_name: str
    question: str
    provider: str = "gemini"
    history: list[dict] = Field(default_factory=list)


class NewsMonitorRequest(BaseModel):
    name: str
    query: str
    schedule_time: str = "20:00"
    timezone: str = "Asia/Kolkata"
    sources: list[str] = Field(default_factory=lambda: ["news"])
    enabled: bool = True


class ChatMessageRequest(BaseModel):
    session_id: str
    content: str
    role: str = "user"


class ChatSessionRequest(BaseModel):
    project_name: str
    provider: str = "gemini"
    domain: Optional[str] = None
    run_async: bool = True


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _create_job(kind: str, payload: Dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        jobs = _read_json(JOBS_PATH, {})
        jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "payload": payload,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "result": None,
            "error": None,
        }
        _write_json(JOBS_PATH, jobs)
    return job_id


def _update_job(job_id: str, **fields: Any) -> None:
    with JOBS_LOCK:
        jobs = _read_json(JOBS_PATH, {})
        if job_id not in jobs:
            return
        jobs[job_id].update(fields)
        jobs[job_id]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(JOBS_PATH, jobs)


async def _run_job(job_id: str, fn, *args, **kwargs) -> None:
    _update_job(job_id, status="running")
    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(*args, **kwargs)
        else:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        _update_job(job_id, status="complete", result=_summarize_result(result))
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))


def _summarize_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    status = result.get("processing_status", {})
    return {
        "project_name": result.get("project_name"),
        "domain": result.get("domain"),
        "processing_status": status,
        "data_sources": list((result.get("data_sources") or {}).keys()),
        "problem_count": len((result.get("agent2_output") or {}).get("problems", [])),
        "insight_count": len((result.get("agent3_output") or {}).get("insights", [])),
        "brief_count": len((result.get("agent4_output") or {}).get("briefs", [])),
    }


def _dump_model(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _pipeline_payload(req: PipelineRunRequest) -> Dict[str, Any]:
    if req.agent1_payload:
        payload = dict(req.agent1_payload)
        payload.setdefault("project_name", req.project_name)
    else:
        payload = {"project_name": req.project_name}
    if req.domain:
        payload["domain"] = req.domain
    return payload


# Mount static assets at root level (so /assets/ paths work)
if STATIC_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_ROOT / "assets")), name="assets")

# Serve frontend at root
@app.get("/")
def frontend() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not built yet")
    return FileResponse(index_path)

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "insights-platform-api"}


@app.get("/projects")
def list_projects() -> Dict[str, Any]:
    projects = []
    if DB_ROOT.exists():
        for db_file in sorted(DB_ROOT.glob("*/db_document.json")):
            doc = _read_json(db_file, {})
            projects.append({
                "project_name": doc.get("project_name") or db_file.parent.name,
                "domain": doc.get("domain"),
                "updated_at": doc.get("ingestion_date"),
                "processing_status": doc.get("processing_status", {}),
            })
    return {"projects": projects}


@app.get("/projects/{project_name}")
def get_project(project_name: str) -> Dict[str, Any]:
    path = project_db_path(project_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return _read_json(path, {})


@app.post("/pipeline/run")
def start_pipeline(req: PipelineRunRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    payload = _pipeline_payload(req)
    job_id = _create_job("pipeline", _dump_model(req))
    background_tasks.add_task(
        _run_job,
        job_id,
        run_pipeline,
        req.project_name,
        req.provider,
        req.start_from,
        req.only,
        payload if req.start_from == "agent1" or req.only == "agent1" else None,
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/transcripts/local")
def ingest_local_transcripts(req: LocalTranscriptRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    input_path = Path(req.input_path)
    if not input_path.exists():
        raise HTTPException(status_code=404, detail="Transcript path not found")

    payload = {
        "project_name": req.project_name,
        "skip_company_profile": True,
        "transcripts": {"input_path": str(input_path)},
    }
    if req.domain:
        payload["domain"] = req.domain

    run_args = (req.project_name, req.provider, "agent1", None, payload)
    if not req.run_async:
        return {"status": "complete", "result": _summarize_result(run_pipeline(*run_args))}

    job_id = _create_job("local_transcripts", _dump_model(req))
    background_tasks.add_task(_run_job, job_id, run_pipeline, *run_args)
    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/google-drive")
def ingest_google_drive(req: GoogleDriveRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    payload = {
        "project_name": req.project_name,
        "skip_company_profile": True,
        "google_drive": {
            "folder_id": req.folder_id,
            "credentials_path": req.credentials_path,
            "token_path": req.token_path,
            "include_existing": req.include_existing,
        },
    }
    if req.domain:
        payload["domain"] = req.domain

    run_args = (req.project_name, req.provider, "agent1", None, payload)
    if not req.run_async:
        return {"status": "complete", "result": _summarize_result(run_pipeline(*run_args))}

    job_id = _create_job("google_drive_transcripts", _dump_model(req))
    background_tasks.add_task(_run_job, job_id, run_pipeline, *run_args)
    return {"job_id": job_id, "status": "queued"}


@app.post("/copilot/ask")
def copilot_ask(req: CopilotRequest) -> Dict[str, Any]:
    try:
        answer, history = ask_copilot(
            project_name=req.project_name,
            question=req.question,
            provider=req.provider,
            conversation_history=req.history,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"answer": answer, "history": history}


@app.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    with JOBS_LOCK:
        jobs = list(_read_json(JOBS_PATH, {}).values())
    return {"jobs": jobs}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    with JOBS_LOCK:
        jobs = _read_json(JOBS_PATH, {})
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/news/monitors")
def list_news_monitors() -> Dict[str, Any]:
    return {"monitors": list(_read_json(NEWS_MONITORS_PATH, {}).values())}


@app.post("/news/monitors")
def upsert_news_monitor(req: NewsMonitorRequest) -> Dict[str, Any]:
    monitors = _read_json(NEWS_MONITORS_PATH, {})
    monitor_id = _slug_id(req.name)
    monitors[monitor_id] = {
        "id": monitor_id,
        **_dump_model(req),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json(NEWS_MONITORS_PATH, monitors)
    return monitors[monitor_id]


def _slug_id(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return safe or str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# ENHANCED CHAT & SESSION ENDPOINTS (NEW)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/chat/session/create")
def create_chat_session(project_name: str) -> Dict[str, Any]:
    """Create a new chat session for a project."""
    try:
        db = get_db()
        session_id = db.create_session(project_name)
        return {
            "session_id": session_id,
            "project_name": project_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/chat/sessions/{project_name}")
def list_chat_sessions(project_name: str) -> Dict[str, Any]:
    """List all chat sessions for a project."""
    try:
        db = get_db()
        sessions = db.list_project_sessions(project_name)
        return {
            "project_name": project_name,
            "sessions": [
                {
                    "session_id": s.id,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "messages_count": s.messages_count,
                    "is_active": s.is_active,
                }
                for s in sessions
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/chat/sessions/{project_name}/{session_id}")
def get_chat_session(project_name: str, session_id: str) -> Dict[str, Any]:
    """Get messages for a specific chat session."""
    try:
        db = get_db()
        session = db.get_session(session_id)
        if not session or session.project_name != project_name:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.get_session_messages(session_id)
        return {
            "session_id": session_id,
            "project_name": project_name,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                }
                for m in messages
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/ask")
def chat_ask(
    project_name: str,
    question: str,
    session_id: Optional[str] = None,
    provider: str = "gemini",
) -> Dict[str, Any]:
    """Ask a question in a chat session (with persistent memory via EnhancedCopilot)."""
    try:
        copilot = create_copilot(project_name, provider)
        answer, session_id = copilot.ask(question, session_id)
        return {
            "session_id": session_id,
            "question": question,
            "answer": answer,
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# DRIVE CONFIG ENDPOINTS (NEW)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/config/drive/add")
def add_drive_folder(
    project_name: str,
    folder_id: str,
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or update a Google Drive folder configuration."""
    try:
        drive_config = get_drive_config()
        entry = drive_config.add_folder(project_name, folder_id, credentials_path, token_path)
        return entry
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config/drive")
def list_drive_folders() -> Dict[str, Any]:
    """List all configured Google Drive folders."""
    try:
        drive_config = get_drive_config()
        folders = drive_config.list_folders()
        return {"folders": folders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config/drive/{project_name}")
def get_drive_folder(project_name: str) -> Dict[str, Any]:
    """Get Google Drive folder configuration for a project."""
    try:
        drive_config = get_drive_config()
        entry = drive_config.get_folder(project_name)
        if not entry:
            raise HTTPException(status_code=404, detail="Drive folder not configured for this project")
        return entry
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/config/drive/{project_name}")
def remove_drive_folder(project_name: str) -> Dict[str, Any]:
    """Remove a Google Drive folder configuration."""
    try:
        drive_config = get_drive_config()
        if drive_config.remove_folder(project_name):
            return {"status": "removed", "project_name": project_name}
        raise HTTPException(status_code=404, detail="Drive folder not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# DAILY SUMMARY ENDPOINTS (NEW)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/summaries/{project_name}/trigger")
def trigger_daily_summary(project_name: str, provider: str = "gemini") -> Dict[str, Any]:
    """Manually trigger daily summary generation."""
    try:
        summary = summarize_daily_chats(project_name, provider=provider)
        if summary:
            return {
                "status": "success",
                "project_name": project_name,
                "summary_preview": summary[:200],
            }
        return {
            "status": "no_messages",
            "project_name": project_name,
            "message": "No messages to summarize",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summaries/{project_name}/latest")
def get_latest_summary(project_name: str) -> Dict[str, Any]:
    """Get the latest daily summary for a project."""
    try:
        db = get_db()
        summary = db.get_latest_summary(project_name)
        if not summary:
            return {
                "project_name": project_name,
                "summary": None,
                "message": "No summaries yet",
            }
        return {
            "project_name": project_name,
            "date": summary.date,
            "summary": summary.summary_md,
            "messages_count": summary.messages_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summaries/{project_name}/{date_str}")
def get_summary_by_date(project_name: str, date_str: str) -> Dict[str, Any]:
    """Get summary for a specific date (format: YYYY-MM-DD)."""
    try:
        db = get_db()
        summary = db.get_summary(project_name, date_str)
        if not summary:
            return {
                "project_name": project_name,
                "date": date_str,
                "summary": None,
            }
        return {
            "project_name": project_name,
            "date": date_str,
            "summary": summary.summary_md,
            "messages_count": summary.messages_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Initialize on startup
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup_event():
    """Initialize database and services on startup."""
    ensure_all_dirs()
    _ = get_db()  # Initialize database
    _ = get_drive_config()  # Initialize drive config
    print("✓ Services initialized: Database, Drive Config, Vector DB")


# Catch-all for client-side routing - keep this after every API route.
@app.get("/{full_path:path}")
def catch_all(full_path: str) -> FileResponse:
    """Serve index.html for all non-API routes (client-side routing)."""
    index_path = STATIC_ROOT / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Not found")
