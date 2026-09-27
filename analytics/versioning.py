"""Version stamps for experiments: agent / prompt / config.

Trades made under different prompts or configs must never be analyzed as
one population (master prompt §16-17). Versions are content hashes, so any
edit to the prompt or settings automatically produces a new version — no
manual bookkeeping.

Prompt version display format: "v<agent_version>+<hash>" e.g. "v0.2.0+a1b2c3d4".
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import agent

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO_ROOT / "agent" / "prompts" / "trading_manager.md"
CONFIG_PATH = REPO_ROOT / "config" / "settings.yaml"


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return "unknown"


def prompt_version(prompt_path: Path | None = None) -> str:
    return _file_hash(prompt_path or PROMPT_PATH)


def config_version(config_path: Path | None = None) -> str:
    return _file_hash(config_path or CONFIG_PATH)


def agent_version() -> str:
    return agent.__version__


def current_versions() -> dict[str, str]:
    return {
        "agent_version": agent_version(),
        "prompt_version": prompt_version(),
        "config_version": config_version(),
    }
