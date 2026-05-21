"""
drive_config.py
===============
Manage Google Drive folder configurations for transcript ingestion.

Allows users to configure multiple drive folders and track sync status.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

try:
    from agents.path import DRIVE_CONFIG_FILE, ensure_all_dirs
except ModuleNotFoundError:
    from paths import DRIVE_CONFIG_FILE, ensure_all_dirs

log = logging.getLogger("drive_config")


class DriveConfig:
    """Manages Google Drive folder configurations."""

    def __init__(self, config_path: Path = DRIVE_CONFIG_FILE):
        self.config_path = config_path
        ensure_all_dirs()
        self.config = self._load()

    def _load(self) -> Dict[str, Any]:
        """Load config from JSON file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.error(f"Failed to load config: {e}")
        return {}

    def _save(self):
        """Save config to JSON file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        log.info(f"Saved drive config to {self.config_path}")

    def add_folder(
        self,
        project_name: str,
        folder_id: str,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add or update a Google Drive folder configuration.

        Args:
            project_name: Project identifier
            folder_id: Google Drive folder ID
            credentials_path: Path to Google service account JSON
            token_path: Path to OAuth token

        Returns:
            Saved config entry
        """
        entry = {
            "project_name": project_name,
            "folder_id": folder_id,
            "credentials_path": credentials_path,
            "token_path": token_path,
            "created_at": datetime.utcnow().isoformat(),
            "last_sync": None,
            "last_sync_count": 0,
            "enabled": True,
        }

        self.config[project_name] = entry
        self._save()
        log.info(f"Added drive config for {project_name}: {folder_id}")
        return entry

    def get_folder(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a project."""
        return self.config.get(project_name)

    def list_folders(self) -> List[Dict[str, Any]]:
        """List all configured folders."""
        return list(self.config.values())

    def update_sync_status(self, project_name: str, count: int) -> bool:
        """Update last sync timestamp and count."""
        if project_name not in self.config:
            return False

        self.config[project_name]["last_sync"] = datetime.utcnow().isoformat()
        self.config[project_name]["last_sync_count"] = count
        self._save()
        return True

    def remove_folder(self, project_name: str) -> bool:
        """Remove a drive configuration."""
        if project_name in self.config:
            del self.config[project_name]
            self._save()
            log.info(f"Removed drive config for {project_name}")
            return True
        return False

    def disable_folder(self, project_name: str) -> bool:
        """Disable a folder without deleting config."""
        if project_name in self.config:
            self.config[project_name]["enabled"] = False
            self._save()
            log.info(f"Disabled drive config for {project_name}")
            return True
        return False

    def enable_folder(self, project_name: str) -> bool:
        """Re-enable a folder."""
        if project_name in self.config:
            self.config[project_name]["enabled"] = True
            self._save()
            log.info(f"Enabled drive config for {project_name}")
            return True
        return False


# Global instance
_drive_config_instance: Optional[DriveConfig] = None


def get_drive_config() -> DriveConfig:
    """Get or create singleton drive config instance."""
    global _drive_config_instance
    if _drive_config_instance is None:
        _drive_config_instance = DriveConfig()
    return _drive_config_instance

