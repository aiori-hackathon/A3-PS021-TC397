"""Configuration from environment variables with per-mode defaults. See docs/CONFIG.md.

`MODE=demo` or `MODE=test` picks the default column; any variable set in the
environment overrides it. Call load() at process start (or in tests) rather
than reading os.environ directly, so every process agrees on the values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PER_MODE: dict[str, dict[str, object]] = {
    "demo": {
        "LEASE_SECONDS": 6.0,
        "HEARTBEAT_SECONDS": 1.0,
        "WATCHDOG_TICK": 0.5,
        "DISPATCH_TICK": 0.5,
        "STARTUP_GRACE": 6.0,
        "POLL_INTERVAL": 1.0,
        "STEP_DELAY": 1.5,
        "HTTP_TIMEOUT": 5.0,
        "MAX_ATTEMPTS": 3,
        "RETRY_BACKOFF_MAX": 2.0,
        "BRIDGE_PORT": 8000,
        "AGENT_A_PORT": 8001,
        "AGENT_B_PORT": 8002,
        "ACTIONS_PORT": 8003,
        "AGENT_B2_PORT": 8004,
        "SUPERVISOR_PORT": 8009,
        "DATA_DIR": "data/demo",
        "LOG_DIR": "logs/demo",
        "AUTO_CONFIRM": False,
    },
    "test": {
        "LEASE_SECONDS": 2.0,
        "HEARTBEAT_SECONDS": 0.5,
        "WATCHDOG_TICK": 0.2,
        "DISPATCH_TICK": 0.1,
        "STARTUP_GRACE": 2.0,
        "POLL_INTERVAL": 0.1,
        "STEP_DELAY": 0.0,
        "HTTP_TIMEOUT": 1.0,
        "MAX_ATTEMPTS": 3,
        "RETRY_BACKOFF_MAX": 0.5,
        "BRIDGE_PORT": 9000,
        "AGENT_A_PORT": 9001,
        "AGENT_B_PORT": 9002,
        "ACTIONS_PORT": 9003,
        "AGENT_B2_PORT": 9004,
        "SUPERVISOR_PORT": 9009,
        "DATA_DIR": "data/test",
        "LOG_DIR": "logs/test",
        "AUTO_CONFIRM": True,
    },
}

COMMON: dict[str, object] = {
    "AGENT_ID": "",
    "CRASH_AT": "never",
    "CRASH_MODE": "once",
    "ROUTER": "rules",
    "OLLAMA_URL": "http://localhost:11434",
    "LLM_MODEL": "qwen3.5:4b",
    "LLM_TIMEOUT": 8.0,
    "DISABLE_DEDUP": False,
    "AUTO_RESTART": False,
    "AUTO_RESTART_DELAY": 2.0,
    "CORS_ORIGINS": "http://localhost:8000,http://127.0.0.1:8000",
    "TEST_MODE": "fast",
    "CHAOS_SEED": 42,
}


@dataclass(frozen=True)
class Settings:
    mode: str
    lease_seconds: float
    heartbeat_seconds: float
    watchdog_tick: float
    dispatch_tick: float
    startup_grace: float
    poll_interval: float
    step_delay: float
    http_timeout: float
    max_attempts: int
    retry_backoff_max: float
    bridge_port: int
    agent_a_port: int
    agent_b_port: int
    actions_port: int
    agent_b2_port: int
    supervisor_port: int
    data_dir: Path
    log_dir: Path
    auto_confirm: bool
    agent_id: str
    crash_at: str
    crash_mode: str
    router: str
    ollama_url: str
    llm_model: str
    llm_timeout: float
    disable_dedup: bool
    auto_restart: bool
    auto_restart_delay: float
    cors_origins: tuple[str, ...]
    test_mode: str
    chaos_seed: int

    def url(self, port: int) -> str:
        return f"http://127.0.0.1:{port}"


def _convert(default: object, raw: str) -> object:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def load(mode: str | None = None, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    mode = mode or env.get("MODE", "demo")
    if mode not in PER_MODE:
        raise ValueError(f"MODE must be one of {sorted(PER_MODE)}, not {mode!r}")
    values: dict[str, object] = {}
    for name, default in {**PER_MODE[mode], **COMMON}.items():
        raw = env.get(name)
        values[name.lower()] = default if raw in (None, "") else _convert(default, raw)
    for key in ("data_dir", "log_dir"):
        path = Path(str(values[key]))
        values[key] = path if path.is_absolute() else ROOT / path
    values["cors_origins"] = tuple(
        o.strip() for o in str(values["cors_origins"]).split(",") if o.strip()
    )
    return Settings(mode=mode, **values)
