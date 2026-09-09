from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectContext:
    root: Path
    project_id: str
    github_repository: str | None = None

    @property
    def scope(self) -> str:
        return f"project:{self.project_id}"


def resolve_project_context(root: Path) -> ProjectContext:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Selected project root does not exist")
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Project root must be inside a Git repository") from exc
    root = Path(top).resolve()
    remote = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=root, text=True, capture_output=True, check=False).stdout.strip()
    repository = None
    match = re.search(r"github\.com[/:]([^/ :]+/[^/ .]+?)(?:\.git)?$", remote, re.I)
    if match:
        repository = match.group(1)
    project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    return ProjectContext(root=root, project_id=project_id, github_repository=repository)
