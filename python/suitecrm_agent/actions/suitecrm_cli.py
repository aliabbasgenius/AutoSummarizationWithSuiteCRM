"""Wrappers around SuiteCRM CLI maintenance commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from ..config import ProjectSettings
from ..utils import console


class SuiteCRMCLI:
    """Convenience wrapper for SuiteCRM maintenance commands."""

    def __init__(self, settings: ProjectSettings):
        self.settings = settings
        self.cli_entry = settings.suitecrm_root / "cli" / "maintenance.php"

    def run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if not self.cli_entry.exists():
            raise FileNotFoundError(f"SuiteCRM CLI entry not found at {self.cli_entry}")

        command = ["php", str(self.cli_entry), *args]
        console().print(f"[magenta]Running SuiteCRM CLI command: {' '.join(command)}[/magenta]")
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def clear_cache(self) -> subprocess.CompletedProcess[str]:
        return self.run(["clear-cache"])

    def quick_repair(self) -> subprocess.CompletedProcess[str]:
        return self.run(["quick-repair"])

    def status(self) -> subprocess.CompletedProcess[str]:
        return self.run(["status"])

    def silent_upgrade(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run(["silent-upgrade", *arguments])
