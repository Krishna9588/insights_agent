from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = Path(__file__).resolve().parent
DB_ROOT = AGENTS_ROOT / "database_mock"
STATE_ROOT = PROJECT_ROOT / "data" / "state"
DATA_ROOT = PROJECT_ROOT / "data"
TRANSCRIPT_ROOT = PROJECT_ROOT / "transcript_input"

# Chat & Session Storage
SESSIONS_ROOT = DATA_ROOT / "sessions"
SUMMARIES_ROOT = DATA_ROOT / "summaries"
VECTORS_ROOT = DATA_ROOT / "vectors"
DB_FILE = DATA_ROOT / "insights.db"  # SQLite database

# Config
CONFIG_FILE = STATE_ROOT / "config.json"
DRIVE_CONFIG_FILE = STATE_ROOT / "drive_config.json"


def project_db_path(project_name: str) -> Path:
    return DB_ROOT / project_name / "db_document.json"


def project_dir(project_name: str) -> Path:
    return DB_ROOT / project_name


def project_sessions_dir(project_name: str) -> Path:
    """Directory for storing chat sessions for a project."""
    path = SESSIONS_ROOT / project_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_summaries_dir(project_name: str) -> Path:
    """Directory for storing daily summaries for a project."""
    path = SUMMARIES_ROOT / project_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_file(project_name: str, session_id: str) -> Path:
    """Path to a specific chat session file."""
    return project_sessions_dir(project_name) / f"{session_id}.jsonl"


def summary_file(project_name: str, date_str: str) -> Path:
    """Path to daily summary markdown file (e.g., 2026-05-17.md)."""
    return project_summaries_dir(project_name) / f"{date_str}.md"


def ensure_all_dirs():
    """Create all required directories."""
    for path in [
        DB_ROOT,
        STATE_ROOT,
        DATA_ROOT,
        SESSIONS_ROOT,
        SUMMARIES_ROOT,
        VECTORS_ROOT,
        TRANSCRIPT_ROOT,
    ]:
        path.mkdir(parents=True, exist_ok=True)

