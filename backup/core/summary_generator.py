"""
summary_generator.py
====================
Automatic daily summary generation for chat sessions.

Runs at configured time (default 11:59 PM) to summarize all active conversations
and generate context markdown for next day's chats.
"""

import logging
import asyncio
import json
from datetime import datetime, date, time
from typing import Optional
import schedule
import pytz

try:
    from core.database import get_db, ChatMessage
    from agents.path import summary_file
    from agents.model_connect import call_llm
except ModuleNotFoundError:
    from database import get_db, ChatMessage
    from paths import summary_file
    try:
        from model_connect import call_llm
    except:
        call_llm = None

log = logging.getLogger("summary_generator")


def format_messages_for_summary(messages: list[ChatMessage]) -> str:
    """Format chat messages into readable text for summarization."""
    if not messages:
        return "No messages in this session."

    formatted = []
    for msg in messages:
        role = "You" if msg.role == "user" else "Assistant"
        formatted.append(f"{role}: {msg.content}\n")

    return "".join(formatted)


def generate_summary_prompt(project_name: str, messages_text: str, messages_count: int) -> str:
    """Build prompt for LLM to generate summary."""
    return f"""You are a research assistant summarizing a team's daily discussion.

Project: {project_name}
Messages today: {messages_count}

CONVERSATION:
{messages_text}

---

Generate a concise summary (200-300 words) highlighting:
1. Main topics discussed
2. Key decisions made
3. Action items / next steps
4. Insights discovered
5. Risks or concerns raised

Format as markdown with clear sections."""


def summarize_daily_chats(
    project_name: str,
    date_str: Optional[str] = None,
    provider: str = "gemini",
) -> Optional[str]:
    """
    Generate and save daily summary for a project.

    Args:
        project_name: Project identifier
        date_str: Date in YYYY-MM-DD format (defaults to today)
        provider: LLM provider

    Returns:
        Generated summary markdown or None if failed
    """
    if date_str is None:
        date_str = date.today().isoformat()

    db = get_db()

    # Get all sessions for this project and date
    sessions = db.list_project_sessions(project_name)
    if not sessions:
        log.warning(f"No sessions found for {project_name} on {date_str}")
        return None

    all_messages = []
    total_count = 0

    for session in sessions:
        session_date = session.created_at.split("T")[0]
        if session_date == date_str:
            messages = db.get_session_messages(session.id)
            all_messages.extend(messages)
            total_count += len(messages)

    if not all_messages:
        log.info(f"No messages for {project_name} on {date_str}")
        return None

    # Format for LLM
    messages_text = format_messages_for_summary(all_messages)
    prompt = generate_summary_prompt(project_name, messages_text, total_count)

    # Generate summary
    try:
        summary_md = call_llm(
            prompt=prompt,
            system_prompt="You are a expert research analyst. Generate clear, actionable summaries.",
            provider=provider,
            json_mode=False,
        )
    except Exception as e:
        log.error(f"Failed to generate summary: {e}")
        return None

    # Save to database
    db.save_summary(project_name, date_str, summary_md, total_count)

    # Also save markdown file
    try:
        summary_path = summary_file(project_name, date_str)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(f"# Daily Summary — {project_name}\n")
            f.write(f"**Date:** {date_str}\n")
            f.write(f"**Messages:** {total_count}\n")
            f.write("---\n\n")
            f.write(summary_md)
        log.info(f"Summary saved to {summary_path}")
    except Exception as e:
        log.error(f"Failed to save summary file: {e}")

    return summary_md


class SummaryScheduler:
    """Background scheduler for automatic daily summaries."""

    def __init__(self, project_names: list[str], time_str: str = "23:59", timezone_str: str = "UTC"):
        """
        Args:
            project_names: List of projects to summarize
            time_str: Time in HH:MM format (24-hour)
            timezone_str: Timezone identifier
        """
        self.project_names = project_names
        self.time_str = time_str
        self.timezone = pytz.timezone(timezone_str)
        self.is_running = False

    def schedule_jobs(self):
        """Schedule daily summary jobs."""
        for project_name in self.project_names:
            schedule.every().day.at(self.time_str).do(
                summarize_daily_chats,
                project_name=project_name,
                provider="gemini",
            )
            log.info(f"Scheduled daily summary for {project_name} at {self.time_str}")

    def start(self):
        """Start background scheduler thread."""
        self.schedule_jobs()
        self.is_running = True

        async def run_scheduler():
            while self.is_running:
                schedule.run_pending()
                await asyncio.sleep(60)  # Check every minute

        return run_scheduler()

    def stop(self):
        """Stop scheduler."""
        self.is_running = False
        schedule.clear()

    def trigger_now(self, project_name: str):
        """Manually trigger summary for specific project."""
        log.info(f"Triggering manual summary for {project_name}")
        return summarize_daily_chats(project_name)


# Global scheduler instance
_scheduler: Optional[SummaryScheduler] = None


def get_scheduler(project_names: Optional[list[str]] = None) -> SummaryScheduler:
    """Get or create scheduler instance."""
    global _scheduler
    if _scheduler is None:
        if not project_names:
            project_names = []
        _scheduler = SummaryScheduler(project_names, time_str="23:59", timezone_str="Asia/Kolkata")
    return _scheduler


def start_scheduler(project_names: list[str]):
    """Initialize and start the scheduler."""
    scheduler = get_scheduler(project_names)
    return scheduler.start()

