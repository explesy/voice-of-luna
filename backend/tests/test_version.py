import re
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__
from app.main import app


def test_version_format():
    assert isinstance(__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?$", __version__)


def test_pyproject_version_matches():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    pyproject_version = data["project"]["version"]
    assert __version__ == pyproject_version


def test_app_version_matches():
    assert app.version == __version__


def test_runtime_endpoint_includes_version(monkeypatch):
    from app.codex import CodexAppServer, RuntimeStatus

    async def fake_status(self, use_cache=True):
        return RuntimeStatus(available=True, detail="Ready")

    monkeypatch.setattr(CodexAppServer, "status", fake_status)

    client = TestClient(app)
    response = client.get("/api/runtime")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == __version__
    assert data["available"] is True
