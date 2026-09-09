from pathlib import Path

import pytest

from app.project_context import resolve_project_context


def test_project_context_requires_git_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Git repository"):
        resolve_project_context(tmp_path)
