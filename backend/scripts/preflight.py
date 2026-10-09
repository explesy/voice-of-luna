#!/usr/bin/env python3
"""Runtime preflight checks for Voice of Luna.

Verifies local environment prerequisites (Codex CLI login, Whisper runtime/models,
TTS voices, and port availability) before booting the application server.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Terminal ANSI styling
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class CheckResult:
    category: str
    name: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    remediation: str | None = None


def check_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if port is available for binding, False if already occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    # 1. Python runtime
    py_version = sys.version_info
    if py_version >= (3, 12):
        results.append(CheckResult(
            category="Runtime",
            name="Python",
            status="OK",
            message=f"v{py_version.major}.{py_version.minor}.{py_version.micro} (>= 3.12)",
        ))
    else:
        results.append(CheckResult(
            category="Runtime",
            name="Python",
            status="FAIL",
            message=f"v{py_version.major}.{py_version.minor}.{py_version.micro} is unsupported (requires >= 3.12)",
            remediation="Install Python 3.12+ using uv: `uv python install 3.12`",
        ))

    # 2. Critical dependencies
    try:
        import fastapi
        import uvicorn
        import httpx
        results.append(CheckResult(
            category="Dependencies",
            name="Core packages",
            status="OK",
            message=f"FastAPI {fastapi.__version__}, Uvicorn {uvicorn.__version__}, HTTPX {httpx.__version__}",
        ))
    except ImportError as exc:
        results.append(CheckResult(
            category="Dependencies",
            name="Core packages",
            status="FAIL",
            message=f"Missing backend dependency: {exc}",
            remediation="Synchronize virtual environment: `make setup` or `cd backend && uv sync`",
        ))

    # 3. Local Codex CLI & Auth
    codex_bin = shutil.which("codex")
    if codex_bin:
        try:
            res = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = f"{res.stdout}\n{res.stderr}".strip()
            if res.returncode == 0 and "Logged in" in out:
                first_line = [line.strip() for line in out.splitlines() if "Logged in" in line][0]
                results.append(CheckResult(
                    category="Codex LLM",
                    name="Codex CLI auth",
                    status="OK",
                    message=f"{first_line} ({codex_bin})",
                ))
            else:
                results.append(CheckResult(
                    category="Codex LLM",
                    name="Codex CLI auth",
                    status="FAIL",
                    message="Codex CLI is installed but not authenticated",
                    remediation="Authenticate your local Codex CLI: run `codex login` in terminal.",
                ))
        except subprocess.TimeoutExpired:
            results.append(CheckResult(
                category="Codex LLM",
                name="Codex CLI auth",
                status="WARN",
                message="Codex login check timed out (>5s)",
                remediation="Verify Codex CLI response manually: `codex login status`",
            ))
        except Exception as exc:
            results.append(CheckResult(
                category="Codex LLM",
                name="Codex CLI auth",
                status="FAIL",
                message=f"Error checking Codex status: {exc}",
                remediation="Ensure Codex CLI functions properly: `codex --version`",
            ))
    else:
        results.append(CheckResult(
            category="Codex LLM",
            name="Codex CLI",
            status="FAIL",
            message="Executable 'codex' not found in PATH",
            remediation="Install OpenAI Codex CLI: `npm install -g @openai/codex` or verify PATH.",
        ))

    # 4. Whisper STT runtime and model
    whisper_srv = shutil.which("whisper-server")
    whisper_cli = shutil.which("whisper-cli")
    if whisper_srv or whisper_cli:
        binary_name = "whisper-server" if whisper_srv else "whisper-cli"
        binary_path = whisper_srv or whisper_cli
        results.append(CheckResult(
            category="Whisper STT",
            name="Whisper binary",
            status="OK",
            message=f"{binary_name} ({binary_path})",
        ))
    else:
        results.append(CheckResult(
            category="Whisper STT",
            name="Whisper binary",
            status="WARN",
            message="Neither whisper-server nor whisper-cli found in PATH",
            remediation="Install whisper.cpp via Homebrew: `brew install whisper-cpp`",
        ))

    # Search for Whisper weights
    whisper_search = [
        PROJECT_ROOT / "data/models",
        BACKEND_ROOT / "models",
        Path.home() / ".cache/voice-of-luna/models",
    ]
    whisper_found = []
    for d in whisper_search:
        if d.is_dir():
            for f in d.glob("ggml-*.bin"):
                if f.stat().st_size > 0:
                    whisper_found.append(f)

    if whisper_found:
        names = ", ".join(f.name for f in whisper_found[:3])
        results.append(CheckResult(
            category="Whisper STT",
            name="Acoustic models",
            status="OK",
            message=f"Found {len(whisper_found)} model file(s) ({names})",
        ))
    else:
        results.append(CheckResult(
            category="Whisper STT",
            name="Acoustic models",
            status="WARN",
            message="No ggml-*.bin weights found in data/models or cache",
            remediation="Download Whisper weights: `curl -L https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin -o data/models/ggml-small.bin`",
        ))

    # 4b. Streaming STT (optional sherpa-onnx engines)
    try:
        import importlib.util

        sherpa_ok = importlib.util.find_spec("sherpa_onnx") is not None
    except Exception:
        sherpa_ok = False
    if sherpa_ok:
        streaming_root = BACKEND_ROOT / "models" / "streaming-stt"
        installed = [
            d.name
            for d in streaming_root.iterdir()
            if d.is_dir()
        ] if streaming_root.is_dir() else []
        if installed:
            results.append(CheckResult(
                category="Streaming STT",
                name="sherpa-onnx engines",
                status="OK",
                message=f"{len(installed)} streaming model(s) installed ({', '.join(sorted(installed)[:2])})",
            ))
        else:
            results.append(CheckResult(
                category="Streaming STT",
                name="sherpa-onnx engines",
                status="WARN",
                message="sherpa-onnx present, no streaming models installed yet (auto-download on first use)",
            ))
    else:
        results.append(CheckResult(
            category="Streaming STT",
            name="sherpa-onnx engines",
            status="WARN",
            message="sherpa-onnx not installed; live transcript will fall back to batch Whisper",
            remediation="Install dependencies: `cd backend && uv sync`",
        ))

    # 5. Local TTS voices (Piper / Silero / macOS)
    tts_dirs = [
        BACKEND_ROOT / "models",
        Path.home() / ".cache/voice-of-luna/models",
    ]
    piper_models = []
    silero_models = []
    for d in tts_dirs:
        if d.is_dir():
            piper_models.extend(d.glob("*.onnx"))
            silero_models.extend(d.glob("silero_*.pt"))

    macos_say = shutil.which("say") is not None
    tts_summary = []
    if piper_models:
        tts_summary.append(f"Piper ({len(piper_models)} onnx)")
    if silero_models:
        tts_summary.append(f"Silero ({len(silero_models)} pt)")
    if macos_say:
        tts_summary.append("macOS system")

    if tts_summary:
        results.append(CheckResult(
            category="TTS Engines",
            name="Speech synthesis",
            status="OK",
            message=", ".join(tts_summary),
        ))
    else:
        results.append(CheckResult(
            category="TTS Engines",
            name="Speech synthesis",
            status="WARN",
            message="No local neural models found in backend/models (fallback to Edge TTS)",
            remediation="Download offline voice models via Web UI or curl from HuggingFace.",
        ))

    # 6. Port checks
    app_port = int(os.environ.get("PORT", "8000"))
    if check_port_free(app_port):
        results.append(CheckResult(
            category="Networking",
            name=f"App port {app_port}",
            status="OK",
            message="Available",
        ))
    else:
        results.append(CheckResult(
            category="Networking",
            name=f"App port {app_port}",
            status="WARN",
            message=f"Port {app_port} is currently in use (server may already be running)",
            remediation=f"Stop the existing process on port {app_port} or specify another port via PORT=...",
        ))

    whisper_port = int(os.environ.get("VOICE_OF_LUNA_WHISPER_PORT", "8089"))
    if check_port_free(whisper_port):
        results.append(CheckResult(
            category="Networking",
            name=f"Whisper port {whisper_port}",
            status="OK",
            message="Available (ready for server spawn)",
        ))
    else:
        results.append(CheckResult(
            category="Networking",
            name=f"Whisper port {whisper_port}",
            status="OK",
            message="Resident whisper-server process already active",
        ))

    return results


def print_report(results: list[CheckResult]) -> bool:
    print(f"\n{BOLD}{CYAN}=== VOICE OF LÚNA // PREFLIGHT DIAGNOSTICS ==={RESET}\n")

    has_fail = False
    remediations: list[tuple[str, str]] = []

    for res in results:
        if res.status == "OK":
            tag = f"{GREEN}[OK]{RESET}"
        elif res.status == "WARN":
            tag = f"{YELLOW}[WARN]{RESET}"
        else:
            tag = f"{RED}[FAIL]{RESET}"
            has_fail = True

        print(f"  {tag} {BOLD}{res.category:14}{RESET} · {res.name:20} -> {res.message}")
        if res.remediation and res.status in ("WARN", "FAIL"):
            remediations.append((res.name, res.remediation))

    print()
    if remediations:
        print(f"{BOLD}{YELLOW}--- RECOMMENDED ACTIONS ---{RESET}")
        for name, rem in remediations:
            print(f"  • {BOLD}{name}{RESET}: {rem}")
        print()

    if has_fail:
        print(f"{RED}{BOLD}Preflight check failed. Please resolve the blockers above before launching.{RESET}\n")
        return False

    print(f"{GREEN}{BOLD}All critical preflight checks passed. Ready for launch.{RESET}\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Run checks and exit without starting")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    results = run_checks()
    passed = print_report(results)

    if args.strict:
        has_warn = any(r.status == "WARN" for r in results)
        if has_warn:
            passed = False

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
