"""Typed environment configuration for LayaWatch.

The variable list comes from the configuration table in ``docs/architecture.md`` section 6 (the
``LAYWATCH_RATELIMIT_*`` row expands to ``docs/rate-limiting.md`` section 8). :meth:`Config.load`
reads only the mapping it is given (or ``os.environ`` when ``environ is None``): it never touches
the filesystem and never logs. Every violation is collected into one :class:`ConfigError` message
joined with ``"; "``, so a bad deployment fails fast with the complete picture.

Doc gaps reported upstream instead of patched into the docs: ``socket_timeout``
(``LAYWATCH_SOCKET_TIMEOUT``) and ``log_level`` (``LAYWATCH_LOG_LEVEL``) have no table row, and
``web_root`` has no variable at all (the dataclass default ``web/out`` always applies).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

_LOG_LEVELS = ("debug", "info", "warning", "error")
_BOOL_VALUES: dict[str, bool] = {
    "0": False, "false": False, "no": False, "off": False,
    "1": True, "true": True, "yes": True, "on": True,
}
_DEFAULT_MODELS = ("english", "multilingual")
_DB_FILENAME = "state.sqlite3"


class ConfigError(Exception):
    """Every configuration violation in one message, separated by ``"; "``."""


@dataclass
class Config:
    """Fully typed configuration; build via :meth:`load` or construct fields directly."""

    # Guaranteed accessors (table rows mapped onto them; gaps noted in the module docstring).
    bind: str = "127.0.0.1"
    port: int = 8050
    socket_timeout: float = 30.0
    max_body_bytes: int = 4194304
    db_path: Path = Path("state.sqlite3")
    web_root: Path = Path("web/out")
    log_level: str = "info"
    log_ring_size: int = 2000
    # docs/architecture.md section 6, in table order.
    state_dir: Path = Path(".")
    models: list[str] = field(default_factory=lambda: list(_DEFAULT_MODELS))
    device: str = "auto"
    english_only: bool = False
    session_secret: str | None = None
    session_ttl: int = 2592000
    trace_sample: float = 1.0
    capture_payloads: bool = False
    payload_max: int = 2048
    retention_traces: int = 10000
    retention_days: int = 7
    ring_traces: int = 1000
    write_queue_max: int = 5000
    stream_tick: int = 3
    bootstrap_owner: str | None = None
    record: bool = True
    api: bool = True
    trust_proxy: bool = False
    # docs/rate-limiting.md section 8 (the section 6 LAYWATCH_RATELIMIT_* row).
    ratelimit_enabled: bool = True
    ratelimit_engine_per_min: int = 0
    ratelimit_ip_per_min: int = 600
    ratelimit_login: int = 5
    ratelimit_login_window: int = 900
    ratelimit_mutation_per_min: int = 60
    ratelimit_playground_per_min: int = 30
    ratelimit_sse_per_session: int = 5
    ratelimit_loopback: bool = False
    engine_max_inflight: int = 4
    engine_queue_max: int = 16

    @classmethod
    def load(cls, environ: Mapping[str, str] | None = None) -> Config:
        """Parse and validate ``environ`` (or ``os.environ`` when ``None``) into a Config.

        Reads only the mapping: no filesystem access, no logging. Raises :class:`ConfigError`
        listing every violation when any variable is malformed or out of range.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        errors: list[str] = []
        # Defaults come from the dataclass fields themselves so the declaration stays the single
        # source of truth for every default value.
        defaults: dict[str, Any] = {
            f.name: (f.default if f.default is not MISSING else f.default_factory())
            for f in fields(cls)
        }

        def parse_int(name: str, attr: str) -> int:
            raw = env.get(name)
            if raw is None:
                return defaults[attr]
            try:
                return int(raw.strip())
            except ValueError:
                errors.append(f"{attr} must be an integer ({name})")
                return defaults[attr]

        def parse_float(name: str, attr: str) -> float:
            raw = env.get(name)
            if raw is None:
                return defaults[attr]
            try:
                return float(raw.strip())
            except ValueError:
                errors.append(f"{attr} must be a number ({name})")
                return defaults[attr]

        def parse_bool(name: str, attr: str) -> bool:
            raw = env.get(name)
            if raw is None:
                return defaults[attr]
            token = raw.strip().lower()
            if token in _BOOL_VALUES:
                return _BOOL_VALUES[token]
            errors.append(f"{attr} must be one of 0|1|true|false|yes|no|on|off ({name})")
            return defaults[attr]

        def parse_str(name: str, attr: str) -> str:
            raw = env.get(name)
            return defaults[attr] if raw is None else raw.strip()

        def parse_opt(name: str, attr: str) -> str | None:
            raw = env.get(name)
            if raw is None:
                return defaults[attr]
            return raw.strip() or None

        def parse_path(name: str, attr: str) -> Path:
            raw = env.get(name)
            return defaults[attr] if raw is None else Path(raw.strip())

        def parse_models(name: str, attr: str) -> list[str]:
            raw = env.get(name)
            if raw is None:
                return list(defaults[attr])
            return [model.strip() for model in raw.split(",") if model.strip()]

        bind = parse_str("LAYWATCH_BIND", "bind")
        if not bind:
            errors.append("bind must be non-empty (LAYWATCH_BIND)")
        port = parse_int("LAYA_PORT", "port")
        if not 1 <= port <= 65535:
            errors.append("port must be between 1 and 65535 (LAYA_PORT)")
        socket_timeout = parse_float("LAYWATCH_SOCKET_TIMEOUT", "socket_timeout")
        if not socket_timeout > 0:
            errors.append("socket_timeout must be greater than 0 (LAYWATCH_SOCKET_TIMEOUT)")
        max_body_bytes = parse_int("LAYWATCH_MAX_BODY", "max_body_bytes")
        if not 1 <= max_body_bytes <= 16777216:
            errors.append("max_body_bytes must be between 1 and 16777216 (LAYWATCH_MAX_BODY)")
        log_level = parse_str("LAYWATCH_LOG_LEVEL", "log_level")
        if log_level not in _LOG_LEVELS:
            errors.append(f"log_level must be one of {'|'.join(_LOG_LEVELS)} (LAYWATCH_LOG_LEVEL)")
        log_ring_size = parse_int("LAYWATCH_RING_LOGS", "log_ring_size")
        if log_ring_size < 100:
            errors.append("log_ring_size must be at least 100 (LAYWATCH_RING_LOGS)")

        state_dir = parse_path("LAYA_STATE_DIR", "state_dir")
        models = parse_models("LAYA_MODELS", "models")
        device = parse_str("LAYA_DEVICE", "device")
        english_only = parse_bool("LAYA_ENGLISH_ONLY", "english_only")
        session_secret = parse_opt("LAYWATCH_SESSION_SECRET", "session_secret")
        session_ttl = parse_int("LAYWATCH_SESSION_TTL", "session_ttl")
        trace_sample = parse_float("LAYWATCH_TRACE_SAMPLE", "trace_sample")
        capture_payloads = parse_bool("LAYWATCH_CAPTURE_PAYLOADS", "capture_payloads")
        payload_max = parse_int("LAYWATCH_PAYLOAD_MAX", "payload_max")
        retention_traces = parse_int("LAYWATCH_RETENTION_TRACES", "retention_traces")
        retention_days = parse_int("LAYWATCH_RETENTION_DAYS", "retention_days")
        ring_traces = parse_int("LAYWATCH_RING_TRACES", "ring_traces")
        write_queue_max = parse_int("LAYWATCH_WRITE_QUEUE_MAX", "write_queue_max")
        stream_tick = parse_int("LAYWATCH_STREAM_TICK", "stream_tick")
        bootstrap_owner = parse_opt("LAYWATCH_BOOTSTRAP_OWNER", "bootstrap_owner")
        record = parse_bool("LAYWATCH_RECORD", "record")
        api = parse_bool("LAYWATCH_API", "api")
        trust_proxy = parse_bool("LAYWATCH_TRUST_PROXY", "trust_proxy")
        ratelimit_enabled = parse_bool("LAYWATCH_RATELIMIT_ENABLED", "ratelimit_enabled")
        ratelimit_engine_per_min = parse_int(
            "LAYWATCH_RATELIMIT_ENGINE_PER_MIN", "ratelimit_engine_per_min"
        )
        ratelimit_ip_per_min = parse_int(
            "LAYWATCH_RATELIMIT_IP_PER_MIN", "ratelimit_ip_per_min"
        )
        ratelimit_login = parse_int("LAYWATCH_RATELIMIT_LOGIN", "ratelimit_login")
        ratelimit_login_window = parse_int(
            "LAYWATCH_RATELIMIT_LOGIN_WINDOW", "ratelimit_login_window"
        )
        ratelimit_mutation_per_min = parse_int(
            "LAYWATCH_RATELIMIT_MUTATION_PER_MIN", "ratelimit_mutation_per_min"
        )
        ratelimit_playground_per_min = parse_int(
            "LAYWATCH_RATELIMIT_PLAYGROUND_PER_MIN", "ratelimit_playground_per_min"
        )
        ratelimit_sse_per_session = parse_int(
            "LAYWATCH_RATELIMIT_SSE_PER_SESSION", "ratelimit_sse_per_session"
        )
        ratelimit_loopback = parse_bool("LAYWATCH_RATELIMIT_LOOPBACK", "ratelimit_loopback")
        engine_max_inflight = parse_int("LAYWATCH_ENGINE_MAX_INFLIGHT", "engine_max_inflight")
        engine_queue_max = parse_int("LAYWATCH_ENGINE_QUEUE_MAX", "engine_queue_max")

        if errors:
            raise ConfigError("; ".join(errors))

        # db_path is derived (the state root contains state.sqlite3); web_root has no variable.
        return cls(
            bind=bind,
            port=port,
            socket_timeout=socket_timeout,
            max_body_bytes=max_body_bytes,
            db_path=state_dir / _DB_FILENAME,
            web_root=defaults["web_root"],
            log_level=log_level,
            log_ring_size=log_ring_size,
            state_dir=state_dir,
            models=models,
            device=device,
            english_only=english_only,
            session_secret=session_secret,
            session_ttl=session_ttl,
            trace_sample=trace_sample,
            capture_payloads=capture_payloads,
            payload_max=payload_max,
            retention_traces=retention_traces,
            retention_days=retention_days,
            ring_traces=ring_traces,
            write_queue_max=write_queue_max,
            stream_tick=stream_tick,
            bootstrap_owner=bootstrap_owner,
            record=record,
            api=api,
            trust_proxy=trust_proxy,
            ratelimit_enabled=ratelimit_enabled,
            ratelimit_engine_per_min=ratelimit_engine_per_min,
            ratelimit_ip_per_min=ratelimit_ip_per_min,
            ratelimit_login=ratelimit_login,
            ratelimit_login_window=ratelimit_login_window,
            ratelimit_mutation_per_min=ratelimit_mutation_per_min,
            ratelimit_playground_per_min=ratelimit_playground_per_min,
            ratelimit_sse_per_session=ratelimit_sse_per_session,
            ratelimit_loopback=ratelimit_loopback,
            engine_max_inflight=engine_max_inflight,
            engine_queue_max=engine_queue_max,
        )
