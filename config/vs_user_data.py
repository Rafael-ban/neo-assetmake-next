"""Per-application VS settings and one-time import of legacy user data."""
from __future__ import annotations
import os
from pathlib import Path
from core.file_utils import atomic_write_bytes, atomic_write_json
from utils.file_utils import get_app_dir

USER_CONFIG_DIR_ENV = "ASSETMAKER_VS_USER_CONFIG_DIR"
MIGRATION_MARKER = ".appdata-migration.json"
USER_FILENAMES = ("vs_runtime.user.json", "trust.json")

def vs_user_config_dir(app_dir: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a path without creating directories or importing settings."""
    isolated = os.environ.get(USER_CONFIG_DIR_ENV)
    if isolated:
        return Path(isolated)
    return Path(get_app_dir() if app_dir is None else app_dir) / ".assetmaker-work" / "config" / "vapoursynth"

def migrate_vs_user_data_once(app_dir: str | os.PathLike[str] | None = None) -> None:
    """Copy missing legacy files, retaining originals and completed partial work."""
    if os.environ.get(USER_CONFIG_DIR_ENV):
        return
    from config.vs_runtime import _override_lock
    target = vs_user_config_dir(app_dir)
    marker = target / MIGRATION_MARKER
    if marker.exists():
        return
    with _override_lock(marker):
        if marker.exists():
            return
        appdata = os.environ.get("APPDATA")
        roaming = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        source = roaming / "ArknightsPassMaker" / "vapoursynth"
        for name in USER_FILENAMES:
            destination = target / name
            if destination.exists():
                continue
            legacy = source / name
            try:
                payload = legacy.read_bytes()
            except FileNotFoundError:
                continue
            with _override_lock(destination):
                if not destination.exists():
                    atomic_write_bytes(destination, payload)
        # Complete empty imports too; failures leave the marker absent for retry.
        atomic_write_json(marker, {"schema_version": 1})
