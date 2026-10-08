"""Unit tests for developer preflight checks and diagnostics."""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import CheckResult, check_port_free, main, print_report, run_checks


def test_check_port_free_available() -> None:
    """An unallocated port should be reported as free."""
    # Find a free port by binding and immediately closing
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert check_port_free(free_port) is True


def test_check_port_free_occupied() -> None:
    """An active bound socket should be detected as occupied (not free)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy_port = s.getsockname()[1]
        assert check_port_free(busy_port) is False


def test_run_checks_structure() -> None:
    """run_checks should return categorized diagnostic items."""
    results = run_checks()
    categories = {r.category for r in results}
    assert "Runtime" in categories
    assert "Dependencies" in categories
    assert "Codex LLM" in categories
    assert "Whisper STT" in categories
    assert "TTS Engines" in categories
    assert "Networking" in categories

    python_check = next(r for r in results if r.name == "Python")
    assert python_check.status == "OK"
    assert "3." in python_check.message


def test_run_checks_codex_not_installed(monkeypatch) -> None:
    """Codex check should report FAIL if binary is not found in PATH."""
    monkeypatch.setattr("shutil.which", lambda cmd: None if cmd == "codex" else "/bin/true")
    results = run_checks()
    codex_check = next(r for r in results if r.name == "Codex CLI")
    assert codex_check.status == "FAIL"
    assert codex_check.remediation is not None
    assert "npm install" in codex_check.remediation


def test_run_checks_whisper_warning(monkeypatch, tmp_path: Path) -> None:
    """Whisper check should report WARN when neither server nor cli binary is found."""
    def fake_which(cmd: str):
        if "whisper" in cmd:
            return None
        return "/usr/bin/python3"

    monkeypatch.setattr("shutil.which", fake_which)
    results = run_checks()
    whisper_bin = next(r for r in results if r.name == "Whisper binary")
    assert whisper_bin.status == "WARN"
    assert "brew install whisper-cpp" in whisper_bin.remediation


def test_print_report_success(capsys) -> None:
    """print_report returns True when no FAIL results exist."""
    items = [
        CheckResult("Runtime", "Python", "OK", "v3.12.0"),
        CheckResult("Networking", "Port", "WARN", "Port in use", "Check port"),
    ]
    passed = print_report(items)
    assert passed is True
    out = capsys.readouterr().out
    assert "VOICE OF LÚNA // PREFLIGHT DIAGNOSTICS" in out
    assert "RECOMMENDED ACTIONS" in out
    assert "All critical preflight checks passed" in out


def test_print_report_failure(capsys) -> None:
    """print_report returns False when any FAIL item is present."""
    items = [
        CheckResult("Codex LLM", "Codex CLI auth", "FAIL", "Unauthenticated", "Run codex login"),
    ]
    passed = print_report(items)
    assert passed is False
    out = capsys.readouterr().out
    assert "Preflight check failed" in out
    assert "Run codex login" in out


def test_main_cli_strict_fails_on_warning(monkeypatch) -> None:
    """--strict flag exits with non-zero if warnings are present."""
    def fake_checks():
        return [
            CheckResult("Runtime", "Python", "OK", "v3.12.0"),
            CheckResult("Whisper STT", "Acoustic models", "WARN", "Missing model", "Download weights"),
        ]

    monkeypatch.setattr("scripts.preflight.run_checks", fake_checks)
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--strict"])
    exit_code = main()
    assert exit_code == 1


def test_main_cli_success(monkeypatch) -> None:
    """Exit code 0 when all checks pass."""
    def fake_checks():
        return [
            CheckResult("Runtime", "Python", "OK", "v3.12.0"),
            CheckResult("Codex LLM", "Codex CLI auth", "OK", "Logged in"),
        ]

    monkeypatch.setattr("scripts.preflight.run_checks", fake_checks)
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--check-only"])
    exit_code = main()
    assert exit_code == 0
