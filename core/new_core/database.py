"""
database.py
===========
SQLite + Supabase vector DB integration for Insights platform.

Manages:
- Chat sessions (SQLite)
- Daily summaries (SQLite)
- Job tracking (SQLite)
- Vector embeddings (Supabase pgvector)
"""

import json
import sqlite3
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict
import uuid

try:
    from agents.path import DB_FILE, ensure_all_dirs
except ModuleNotFoundError:
    from paths import DB_FILE, ensure_all_dirs

log = logging.getLogger("database")


@dataclass
class ChatMessage:
    """Single chat message."""
    id: str
    session_id: str
    role: str  # "user" or "assistant"
    content: str
    timestamp: str  # ISO format


@dataclass
class ChatSession:
    """Conversation session for a project."""
    id: str
    project_name: str
    created_at: str
    updated_at: str
    messages_count: int = 0
    is_active: bool = True


@dataclass
class DailySummary:
    """Auto-generated summary of daily chats."""
    id: str
    project_name: str
    date: str  # YYYY-MM-DD
    summary_md: str
    messages_count: int
    created_at: str


@dataclass
class JobRecord:
    """Track async pipeline jobs."""
    id: str
    project_name: str
    kind: str  # "pipeline", "local_transcripts", "google_drive", etc.
    status: str  # "queued", "running", "complete", "failed"
    progress: int  # 0-100
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Database:
    """SQLite wrapper for sessions, summaries, and jobs."""

    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        ensure_all_dirs()
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    summary_md TEXT NOT NULL,
                    messages_count INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_name, date)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_records (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result TEXT,
                    error TEXT
                )
            """)
            conn.commit()
            log.info(f"Database initialized at {self.db_path}")

    # ─────────────────────────────────────────────────────────────────────────
    # CHAT SESSIONS
    # ─────────────────────────────────────────────────────────────────────────

    def create_session(self, project_name: str) -> str:
        """Create a new chat session. Returns session_id."""
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (id, project_name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, project_name, now, now),
            )
            conn.commit()
        log.info(f"Created session {session_id} for project {project_name}")
        return session_id

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Retrieve a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row:
                return ChatSession(
                    id=row["id"],
                    project_name=row["project_name"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    messages_count=row["messages_count"],
                    is_active=bool(row["is_active"]),
                )
        return None

    def list_project_sessions(self, project_name: str) -> List[ChatSession]:
        """List all sessions for a project, latest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM chat_sessions 
                WHERE project_name = ? 
                ORDER BY updated_at DESC
                """,
                (project_name,),
            ).fetchall()
            return [
                ChatSession(
                    id=row["id"],
                    project_name=row["project_name"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    messages_count=row["messages_count"],
                    is_active=bool(row["is_active"]),
                )
                for row in rows
            ]

    # ─────────────────────────────────────────────────────────────────────────
    # CHAT MESSAGES
    # ─────────────────────────────────────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str) -> str:
        """Add a message to a session. Returns message_id."""
        message_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, now),
            )
            # Update session count and timestamp
            conn.execute(
                """
                UPDATE chat_sessions 
                SET updated_at = ?, messages_count = messages_count + 1
                WHERE id = ?
                """,
                (now, session_id),
            )
            conn.commit()
        return message_id

    def get_session_messages(self, session_id: str) -> List[ChatMessage]:
        """Retrieve all messages in a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM chat_messages 
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            ).fetchall()
            return [
                ChatMessage(
                    id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    content=row["content"],
                    timestamp=row["timestamp"],
                )
                for row in rows
            ]

    # ─────────────────────────────────────────────────────────────────────────
    # DAILY SUMMARIES
    # ─────────────────────────────────────────────────────────────────────────

    def save_summary(self, project_name: str, date_str: str, summary_md: str, messages_count: int):
        """Save daily summary (creates or updates)."""
        summary_id = f"{project_name}_{date_str}"
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_summaries 
                (id, project_name, date, summary_md, messages_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (summary_id, project_name, date_str, summary_md, messages_count, now),
            )
            conn.commit()
        log.info(f"Saved summary for {project_name} on {date_str}")

    def get_summary(self, project_name: str, date_str: str) -> Optional[DailySummary]:
        """Retrieve summary for a specific date."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM daily_summaries 
                WHERE project_name = ? AND date = ?
                """,
                (project_name, date_str),
            ).fetchone()
            if row:
                return DailySummary(
                    id=row["id"],
                    project_name=row["project_name"],
                    date=row["date"],
                    summary_md=row["summary_md"],
                    messages_count=row["messages_count"],
                    created_at=row["created_at"],
                )
        return None

    def get_latest_summary(self, project_name: str) -> Optional[DailySummary]:
        """Retrieve the most recent summary."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM daily_summaries 
                WHERE project_name = ? 
                ORDER BY date DESC LIMIT 1
                """,
                (project_name,),
            ).fetchone()
            if row:
                return DailySummary(
                    id=row["id"],
                    project_name=row["project_name"],
                    date=row["date"],
                    summary_md=row["summary_md"],
                    messages_count=row["messages_count"],
                    created_at=row["created_at"],
                )
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # JOB RECORDS
    # ─────────────────────────────────────────────────────────────────────────

    def create_job(
        self,
        project_name: str,
        kind: str,
        status: str = "queued",
    ) -> str:
        """Create a job record. Returns job_id."""
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO job_records 
                (id, project_name, kind, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, project_name, kind, status, 0, now, now),
            )
            conn.commit()
        return job_id

    def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        """Update job status/progress."""
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if result is not None:
            updates.append("result = ?")
            params.append(json.dumps(result))
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        if updates:
            updates.append("updated_at = ?")
            params.append(datetime.utcnow().isoformat())
            params.append(job_id)

            query = f"UPDATE job_records SET {', '.join(updates)} WHERE id = ?"
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(query, params)
                conn.commit()

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Retrieve a job record."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM job_records WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row:
                result = None
                if row["result"]:
                    result = json.loads(row["result"])
                return JobRecord(
                    id=row["id"],
                    project_name=row["project_name"],
                    kind=row["kind"],
                    status=row["status"],
                    progress=row["progress"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    result=result,
                    error=row["error"],
                )
        return None


# Global database instance
_db_instance: Optional[Database] = None


def get_db() -> Database:
    """Get or create singleton database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance

