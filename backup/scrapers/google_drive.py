"""
google_drive.py
===============
Google Drive connector for transcript ingestion.

This module watches a single Drive folder, downloads newly uploaded transcript
files, records processed file ids in a small JSON state file, and exposes a
helper that can trigger the existing Agent 1 -> 4 pipeline.

Supported source files:
  - Uploaded files: txt, md, csv, json, docx, xlsx, xls, pdf
  - Google Docs / Sheets: exported to docx / xlsx

Required packages:
  pip install google-api-python-client google-auth google-auth-oauthlib
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from agents.path import PROJECT_ROOT, STATE_ROOT, TRANSCRIPT_ROOT
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    STATE_ROOT = PROJECT_ROOT / "data" / "state"
    TRANSCRIPT_ROOT = PROJECT_ROOT / "transcript_input"


log = logging.getLogger("google_drive")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".docx", ".xlsx", ".xls", ".pdf"
}

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}


def _slug(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", value or "project").strip("_").lower()
    return safe or "project"


def _safe_filename(name: str, fallback: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._ -]+", "_", name or fallback).strip()
    return base or fallback


class ProcessedFileStore:
    """Tiny JSON ledger keyed by Google Drive file id."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else STATE_ROOT / "google_drive_processed.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"processed_files": {}, "initialized_folders": {}, "updated_at": None}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("processed_files", {})
            data.setdefault("initialized_folders", {})
            return data
        except Exception:
            log.warning("Could not read Drive state file; starting with a clean ledger.")
            return {"processed_files": {}, "initialized_folders": {}, "updated_at": None}

    def is_processed(self, file_id: str, modified_time: Optional[str]) -> bool:
        record = self.data["processed_files"].get(file_id)
        if not record:
            return False
        return record.get("modified_time") == modified_time

    def mark_processed(self, file_meta: Dict[str, Any], local_path: Optional[str], status: str) -> None:
        self.data["processed_files"][file_meta["id"]] = {
            "name": file_meta.get("name"),
            "mime_type": file_meta.get("mimeType"),
            "modified_time": file_meta.get("modifiedTime"),
            "local_path": local_path,
            "status": status,
            "seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def folder_initialized(self, folder_id: str) -> bool:
        return bool(self.data["initialized_folders"].get(folder_id))

    def mark_folder_initialized(self, folder_id: str) -> None:
        self.data["initialized_folders"][folder_id] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )

    def save(self) -> None:
        self.data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)


def _load_drive_service(credentials_path: Optional[str] = None, token_path: Optional[str] = None):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Google Drive packages are missing. Install google-api-python-client, "
            "google-auth, and google-auth-oauthlib."
        ) from e

    credentials_path = credentials_path or os.getenv("GOOGLE_DRIVE_CREDENTIALS")
    token_path = token_path or os.getenv("GOOGLE_DRIVE_TOKEN") or str(
        STATE_ROOT / "google_drive_token.json"
    )

    if not credentials_path:
        raise ValueError(
            "Google Drive credentials path is required. Set GOOGLE_DRIVE_CREDENTIALS "
            "or pass credentials_path."
        )

    token_file = Path(token_path)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("drive", "v3", credentials=creds)


def _list_folder_files(service, folder_id: str, max_files: Optional[int] = None) -> List[Dict[str, Any]]:
    query = f"'{folder_id}' in parents and trashed = false"
    files: List[Dict[str, Any]] = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                orderBy="modifiedTime desc",
                pageToken=page_token,
                pageSize=min(max_files or 100, 100),
            )
            .execute()
        )
        files.extend(response.get("files", []))
        if max_files and len(files) >= max_files:
            return files[:max_files]
        page_token = response.get("nextPageToken")
        if not page_token:
            return files


def _target_name(file_meta: Dict[str, Any]) -> Optional[str]:
    mime_type = file_meta.get("mimeType")
    name = file_meta.get("name") or file_meta["id"]

    if mime_type in GOOGLE_EXPORTS:
        return _safe_filename(Path(name).stem + GOOGLE_EXPORTS[mime_type][1], file_meta["id"])

    suffix = Path(name).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return _safe_filename(name, file_meta["id"] + suffix)

    return None


def _download_file(service, file_meta: Dict[str, Any], destination: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload

    mime_type = file_meta.get("mimeType")
    if mime_type in GOOGLE_EXPORTS:
        export_mime, _ = GOOGLE_EXPORTS[mime_type]
        request = service.files().export_media(fileId=file_meta["id"], mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_meta["id"])

    destination.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    destination.write_bytes(buffer.getvalue())
    return destination


def sync_google_drive_transcripts(
    project_name: str,
    folder_id: Optional[str],
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
    local_dir: Optional[str] = None,
    state_path: Optional[str] = None,
    include_existing: bool = False,
    max_files: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Download new Drive folder files into a local transcript folder.

    If include_existing=False on the first folder sync, existing files are marked
    as seen and skipped. This prevents a first deployment from reprocessing a
    historical folder unless the user explicitly asks for it.
    """
    if not folder_id:
        return {"status": "error", "message": "folder_id is required", "downloaded_files": []}

    store = ProcessedFileStore(state_path)
    service = _load_drive_service(credentials_path, token_path)
    local_root = Path(local_dir) if local_dir else TRANSCRIPT_ROOT / "google_drive" / _slug(project_name)
    files = _list_folder_files(service, folder_id, max_files=max_files)

    first_sync_skip = not include_existing and not store.folder_initialized(folder_id)
    downloaded: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []

    for file_meta in files:
        target_name = _target_name(file_meta)
        if not target_name:
            unsupported.append({
                "id": file_meta.get("id"),
                "name": file_meta.get("name"),
                "mime_type": file_meta.get("mimeType"),
            })
            continue

        if store.is_processed(file_meta["id"], file_meta.get("modifiedTime")):
            skipped.append({"id": file_meta["id"], "name": file_meta.get("name"), "reason": "already_processed"})
            continue

        if first_sync_skip:
            store.mark_processed(file_meta, None, "skipped_existing")
            skipped.append({"id": file_meta["id"], "name": file_meta.get("name"), "reason": "initial_existing"})
            continue

        local_path = local_root / target_name
        _download_file(service, file_meta, local_path)
        store.mark_processed(file_meta, str(local_path), "downloaded")
        downloaded.append({
            "id": file_meta["id"],
            "name": file_meta.get("name"),
            "mime_type": file_meta.get("mimeType"),
            "modified_time": file_meta.get("modifiedTime"),
            "local_path": str(local_path),
        })

    store.mark_folder_initialized(folder_id)
    store.save()

    return {
        "status": "success",
        "project_name": project_name,
        "folder_id": folder_id,
        "state_path": str(store.path),
        "local_dir": str(local_root),
        "downloaded_count": len(downloaded),
        "skipped_count": len(skipped),
        "unsupported_count": len(unsupported),
        "downloaded_files": downloaded,
        "skipped_files": skipped,
        "unsupported_files": unsupported,
    }


def run_drive_pipeline_once(
    project_name: str,
    folder_id: str,
    provider: str = "gemini",
    domain: Optional[str] = None,
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
    include_existing: bool = False,
) -> Dict[str, Any]:
    """
    Convenience entry point for a scheduler/cron job.

    It delegates Drive sync to Agent 1 and then runs Agents 2-4 so new
    transcript signals become immediately queryable by the copilot.
    """
    from pipeline_v2 import run_pipeline

    payload: Dict[str, Any] = {
        "project_name": project_name,
        "skip_company_profile": True,
        "google_drive": {
            "folder_id": folder_id,
            "credentials_path": credentials_path,
            "token_path": token_path,
            "include_existing": include_existing,
        },
    }
    if domain:
        payload["domain"] = domain

    result = run_pipeline(
        project_name=project_name,
        provider=provider,
        start_from="agent1",
        agent1_payload=payload,
    )

    drive_source = result.get("data_sources", {}).get("google_drive_transcripts", {})
    return {
        "status": "success",
        "project_name": project_name,
        "pipeline_ran": True,
        "drive_processed_files": drive_source.get("processed_files", 0),
        "db_document": result,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync Google Drive transcripts.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--credentials-path", default=None)
    parser.add_argument("--token-path", default=None)
    parser.add_argument("--include-existing", action="store_true")
    args = parser.parse_args()

    print(json.dumps(sync_google_drive_transcripts(
        project_name=args.project,
        folder_id=args.folder_id,
        credentials_path=args.credentials_path,
        token_path=args.token_path,
        include_existing=args.include_existing,
    ), indent=2))
