"""Validation matrix for layawatch.config.

``DOCUMENTED`` hardcodes every variable from the docs/architecture.md section 6 configuration table
(the ``LAYWATCH_RATELIMIT_*`` row expanded per docs/rate-limiting.md section 8) plus the two
reported doc gaps. It is the drift guard: if code drops or retypes a documented variable, this list
fails.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from layawatch.config import Config, ConfigError

# (env var, attribute, sample env value, expected parsed value, expected type)
DOCUMENTED: list[tuple[str, str, str, object, type]] = [
    ("LAYA_STATE_DIR", "state_dir", "/srv/laya", Path("/srv/laya"), Path),
    ("LAYA_PORT", "port", "9001", 9001, int),
    ("LAYWATCH_BIND", "bind", "0.0.0.0", "0.0.0.0", str),
    ("LAYA_MODELS", "models", "english", ["english"], list),
    ("LAYA_DEVICE", "device", "cuda:1", "cuda:1", str),
    ("LAYA_ENGLISH_ONLY", "english_only", "1", True, bool),
    ("LAYWATCH_SESSION_SECRET", "session_secret", "hunter2", "hunter2", str),
    ("LAYWATCH_SESSION_TTL", "session_ttl", "60", 60, int),
    ("LAYWATCH_TRACE_SAMPLE", "trace_sample", "0.25", 0.25, float),
    ("LAYWATCH_CAPTURE_PAYLOADS", "capture_payloads", "1", True, bool),
    ("LAYWATCH_PAYLOAD_MAX", "payload_max", "512", 512, int),
    ("LAYWATCH_RETENTION_TRACES", "retention_traces", "42", 42, int),
    ("LAYWATCH_RETENTION_DAYS", "retention_days", "3", 3, int),
    ("LAYWATCH_RING_TRACES", "ring_traces", "10", 10, int),
    ("LAYWATCH_RING_LOGS", "log_ring_size", "300", 300, int),
    ("LAYWATCH_WRITE_QUEUE_MAX", "write_queue_max", "7", 7, int),
    ("LAYWATCH_STREAM_TICK", "stream_tick", "5", 5, int),
    ("LAYWATCH_BOOTSTRAP_OWNER", "bootstrap_owner", "a@b.c:pw", "a@b.c:pw", str),
    ("LAYWATCH_RECORD", "record", "0", False, bool),
    ("LAYWATCH_API", "api", "0", False, bool),
    ("LAYWATCH_TRUST_PROXY", "trust_proxy", "1", True, bool),
    ("LAYWATCH_RATELIMIT_ENABLED", "ratelimit_enabled", "0", False, bool),
    ("LAYWATCH_RATELIMIT_ENGINE_PER_MIN", "ratelimit_engine_per_min", "120", 120, int),
    ("LAYWATCH_RATELIMIT_IP_PER_MIN", "ratelimit_ip_per_min", "300", 300, int),
    ("LAYWATCH_RATELIMIT_LOGIN", "ratelimit_login", "3", 3, int),
    ("LAYWATCH_RATELIMIT_LOGIN_WINDOW", "ratelimit_login_window", "60", 60, int),
    ("LAYWATCH_RATELIMIT_MUTATION_PER_MIN", "ratelimit_mutation_per_min", "10", 10, int),
    ("LAYWATCH_RATELIMIT_PLAYGROUND_PER_MIN", "ratelimit_playground_per_min", "5", 5, int),
    ("LAYWATCH_RATELIMIT_SSE_PER_SESSION", "ratelimit_sse_per_session", "2", 2, int),
    ("LAYWATCH_RATELIMIT_LOOPBACK", "ratelimit_loopback", "1", True, bool),
    ("LAYWATCH_ENGINE_MAX_INFLIGHT", "engine_max_inflight", "2", 2, int),
    ("LAYWATCH_ENGINE_QUEUE_MAX", "engine_queue_max", "8", 8, int),
    ("LAYWATCH_MAX_BODY", "max_body_bytes", "1024", 1024, int),
    # Doc gaps reported upstream: no row in the section 6 table yet.
    ("LAYWATCH_SOCKET_TIMEOUT", "socket_timeout", "12.5", 12.5, float),
    ("LAYWATCH_LOG_LEVEL", "log_level", "debug", "debug", str),
]

# Every field's documented default; keys must equal Config's field set exactly.
EXPECTED_DEFAULTS: dict[str, object] = {
    "bind": "127.0.0.1",
    "port": 8050,
    "socket_timeout": 30.0,
    "max_body_bytes": 4194304,
    "db_path": Path("state.sqlite3"),
    "web_root": Path("web/out"),
    "log_level": "info",
    "log_ring_size": 2000,
    "state_dir": Path("."),
    "models": ["english", "multilingual"],
    "device": "auto",
    "english_only": False,
    "session_secret": None,
    "session_ttl": 2592000,
    "trace_sample": 1.0,
    "capture_payloads": False,
    "payload_max": 2048,
    "retention_traces": 10000,
    "retention_days": 7,
    "ring_traces": 1000,
    "write_queue_max": 5000,
    "stream_tick": 3,
    "bootstrap_owner": None,
    "record": True,
    "api": True,
    "trust_proxy": False,
    "ratelimit_enabled": True,
    "ratelimit_engine_per_min": 0,
    "ratelimit_ip_per_min": 600,
    "ratelimit_login": 5,
    "ratelimit_login_window": 900,
    "ratelimit_mutation_per_min": 60,
    "ratelimit_playground_per_min": 30,
    "ratelimit_sse_per_session": 5,
    "ratelimit_loopback": False,
    "engine_max_inflight": 4,
    "engine_queue_max": 16,
}


def test_defaults_load_from_empty_environ() -> None:
    cfg = Config.load({})
    assert asdict(cfg) == EXPECTED_DEFAULTS
    assert isinstance(cfg.db_path, Path)
    assert isinstance(cfg.web_root, Path)


def test_db_path_derives_from_state_dir() -> None:
    cfg = Config.load({"LAYA_STATE_DIR": "/srv/state"})
    assert cfg.state_dir == Path("/srv/state")
    assert cfg.db_path == Path("/srv/state/state.sqlite3")


@pytest.mark.parametrize(
    ("env_name", "attr", "raw", "expected", "expected_type"),
    DOCUMENTED,
    ids=[row[0] for row in DOCUMENTED],
)
def test_documented_variable_parses_into_attribute(
    env_name: str,
    attr: str,
    raw: str,
    expected: object,
    expected_type: type,
) -> None:
    cfg = Config.load({env_name: raw})
    value = getattr(cfg, attr)
    assert value == expected
    assert isinstance(value, expected_type)


# (env override, substrings the ConfigError message must mention)
INVALID_CASES: list[tuple[dict[str, str], tuple[str, ...]]] = [
    ({"LAYA_PORT": "not-a-number"}, ("port", "LAYA_PORT")),
    ({"LAYA_PORT": "0"}, ("port", "LAYA_PORT")),
    ({"LAYA_PORT": "65536"}, ("port", "LAYA_PORT")),
    ({"LAYWATCH_BIND": ""}, ("bind", "LAYWATCH_BIND")),
    ({"LAYWATCH_SOCKET_TIMEOUT": "0"}, ("socket_timeout", "LAYWATCH_SOCKET_TIMEOUT")),
    ({"LAYWATCH_SOCKET_TIMEOUT": "-2"}, ("socket_timeout", "LAYWATCH_SOCKET_TIMEOUT")),
    ({"LAYWATCH_SOCKET_TIMEOUT": "soon"}, ("socket_timeout", "LAYWATCH_SOCKET_TIMEOUT")),
    ({"LAYWATCH_MAX_BODY": "0"}, ("max_body_bytes", "LAYWATCH_MAX_BODY")),
    ({"LAYWATCH_MAX_BODY": "16777217"}, ("max_body_bytes", "LAYWATCH_MAX_BODY")),
    ({"LAYWATCH_MAX_BODY": "lots"}, ("max_body_bytes", "LAYWATCH_MAX_BODY")),
    ({"LAYWATCH_LOG_LEVEL": "verbose"}, ("log_level", "LAYWATCH_LOG_LEVEL")),
    ({"LAYWATCH_RING_LOGS": "99"}, ("log_ring_size", "LAYWATCH_RING_LOGS")),
    ({"LAYWATCH_RING_LOGS": "many"}, ("log_ring_size", "LAYWATCH_RING_LOGS")),
    ({"LAYWATCH_TRACE_SAMPLE": "lots"}, ("trace_sample", "LAYWATCH_TRACE_SAMPLE")),
    ({"LAYWATCH_SESSION_TTL": "soon"}, ("session_ttl", "LAYWATCH_SESSION_TTL")),
    ({"LAYWATCH_RECORD": "banana"}, ("record", "LAYWATCH_RECORD")),
    ({"LAYA_ENGLISH_ONLY": "perhaps"}, ("english_only", "LAYA_ENGLISH_ONLY")),
]


@pytest.mark.parametrize(
    ("environ", "mentions"),
    INVALID_CASES,
    ids=[f"{name}={value}" for environ, _ in INVALID_CASES for name, value in environ.items()],
)
def test_invalid_value_raises_config_error_mentioning_variable(
    environ: dict[str, str],
    mentions: tuple[str, ...],
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        Config.load(environ)
    message = str(excinfo.value)
    for name in mentions:
        assert name in message


def test_multiple_violations_aggregate_into_one_message() -> None:
    environ = {
        "LAYA_PORT": "abc",
        "LAYWATCH_SOCKET_TIMEOUT": "0",
        "LAYWATCH_LOG_LEVEL": "verbose",
    }
    with pytest.raises(ConfigError) as excinfo:
        Config.load(environ)
    assert str(excinfo.value) == (
        "port must be an integer (LAYA_PORT); "
        "socket_timeout must be greater than 0 (LAYWATCH_SOCKET_TIMEOUT); "
        "log_level must be one of debug|info|warning|error (LAYWATCH_LOG_LEVEL)"
    )


def test_custom_environ_does_not_read_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAYA_PORT", "9999")
    monkeypatch.setenv("LAYWATCH_LOG_LEVEL", "nonsense")
    cfg = Config.load({})
    assert cfg.port == 8050
    assert cfg.log_level == "info"
    partial = Config.load({"LAYWATCH_BIND": "0.0.0.0"})
    assert partial.port == 8050
    assert partial.bind == "0.0.0.0"


def test_load_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAYA_PORT", "6001")
    assert Config.load().port == 6001
    assert Config.load({"LAYA_PORT": "1234"}).port == 1234


def test_load_never_touches_the_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("load() must not touch the filesystem")

    monkeypatch.setattr(Path, "exists", refuse)
    monkeypatch.setattr(Path, "resolve", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    cfg = Config.load({"LAYA_STATE_DIR": "/no/such/place"})
    assert cfg.db_path == Path("/no/such/place/state.sqlite3")
