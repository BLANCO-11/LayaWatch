"""D-008 runtime rate-limit policy and in-memory token buckets (rate-limiting 2, 6.1, 7, 9).

Policy fields live in the ``settings`` table under ``ratelimit.*`` keys and override the
env-driven config defaults at read time (section 9: the environment provides defaults,
values saved in ``settings`` win), so an edit applies from the next request with no
restart. Field validation is shared by the POST surface: ints carry their bounds, the
login window only accepts the 5m/15m/1h choices the Settings card offers (6.1), and
``0`` means unlimited for the engine-per-key and per-IP guards.

Buckets are token buckets keyed by ``(subject, scope)`` and live in memory only
(sections 2 and 9): the clock is ``time.monotonic`` so wall-clock jumps cannot bypass a
bucket, capacity defaults to the limit, refill is ``limit / window`` tokens per second, a
rejected request does not consume a token, and an accepted one consumes exactly one. A
sweep drops fully refilled buckets once the map grows past ``_SWEEP_AT``, so minting
subjects cannot grow the map without bound; a restart clears buckets, which section 2
documents as acceptable for a single process.

The registry serves the management surface (live usage panel, per-row reset) and exposes
``consume`` for the enforcement layer; nothing in this module enforces a limit by itself.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import sqlite3

    from layawatch.config import Config

#: Subjects kept before a sweep drops every fully refilled bucket (login_limit precedent).
_SWEEP_AT = 1024

#: Bucket scopes, one per rate-limiting section 2 policy tuple (subject x scope x limit).
SCOPES = ("api_key", "ip", "login", "session", "playground", "sse", "engine")

#: The window choices behind the 6.1 "5m, 15m, 1h" select, in seconds.
LOGIN_WINDOWS = (300, 900, 3600)

#: One policy field: API name, settings key, Config attribute, kind, constraint.
#: Kinds: "bool"; "int" with a minimum; "window" with an allowed set of seconds.
_POLICY_FIELDS: tuple[tuple[str, str, str, str, Any], ...] = (
    ("enabled", "ratelimit.enabled", "ratelimit_enabled", "bool", None),
    ("engine_per_min", "ratelimit.engine_per_min", "ratelimit_engine_per_min", "int", 0),
    ("ip_per_min", "ratelimit.ip_per_min", "ratelimit_ip_per_min", "int", 0),
    ("login", "ratelimit.login", "ratelimit_login", "int", 1),
    ("login_window", "ratelimit.login_window", "ratelimit_login_window", "window", LOGIN_WINDOWS),
    ("mutation_per_min", "ratelimit.mutation_per_min", "ratelimit_mutation_per_min", "int", 0),
    (
        "playground_per_min",
        "ratelimit.playground_per_min",
        "ratelimit_playground_per_min",
        "int",
        0,
    ),
    ("engine_max_inflight", "ratelimit.engine_max_inflight", "engine_max_inflight", "int", 1),
    ("engine_queue_max", "ratelimit.engine_queue_max", "engine_queue_max", "int", 1),
    ("loopback_exempt", "ratelimit.loopback", "ratelimit_loopback", "bool", None),
)

_FIELD_BY_NAME: dict[str, tuple[str, str, str, str, Any]] = {
    field[0]: field for field in _POLICY_FIELDS
}

#: Values a stored TEXT row accepts as true (api/auth.py reads ``ratelimit.enabled`` so).
_TRUE_TEXT = frozenset({"1", "true", "yes", "on"})


class PolicyError(ValueError):
    """Invalid policy update: ``field`` names the offending control for the error envelope."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def effective_policy(conn: sqlite3.Connection, config: Config) -> dict[str, int | bool]:
    """Config defaults overridden by the stored ``ratelimit.*`` rows (rate-limiting 9).

    A hand-edited row that no longer validates falls back to the config default instead
    of failing the read view; ``validated_update`` rejects the same value on write.
    """
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'ratelimit.%'"
    ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    policy: dict[str, int | bool] = {}
    for name, key, attr, kind, constraint in _POLICY_FIELDS:
        default = getattr(config, attr)
        value: int | bool = bool(default) if kind == "bool" else int(default)
        if key in stored:
            try:
                value = _parse(name, kind, constraint, stored[key], from_text=True)
            except PolicyError:
                pass  # corrupt row: the env default is the safer answer for a read view
        policy[name] = value
    return policy


def validated_update(payload: Any) -> dict[str, int | bool]:
    """Validate a POST body against the policy fields; raises ``PolicyError`` (7: partial
    update, validated). Every error names the offending field for the error envelope."""
    if not isinstance(payload, dict):
        raise PolicyError("body", "request body must be a JSON object")
    if not payload:
        raise PolicyError("body", "no policy fields to update")
    updates: dict[str, int | bool] = {}
    for name, raw in payload.items():
        spec = _FIELD_BY_NAME.get(name)
        if spec is None:
            raise PolicyError(name, f"unknown policy field {name!r}")
        _, _, _, kind, constraint = spec
        updates[name] = _parse(name, kind, constraint, raw, from_text=False)
    return updates


def upsert_policy(conn: sqlite3.Connection, updates: Mapping[str, int | bool], actor: str) -> None:
    """Write the validated fields as settings rows; no commit (the caller owns the txn)."""
    now = time.time()
    for name, value in updates.items():
        key = _FIELD_BY_NAME[name][1]
        text = ("1" if value else "0") if isinstance(value, bool) else str(value)
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (key, text, now, actor),
        )


def _parse(name: str, kind: str, constraint: Any, raw: Any, *, from_text: bool) -> int | bool:
    """Coerce one field to its typed value or raise ``PolicyError`` naming ``name``."""
    if kind == "bool":
        if from_text:
            return str(raw).strip().lower() in _TRUE_TEXT
        if isinstance(raw, bool):
            return raw
        raise PolicyError(name, f"'{name}' must be a boolean")
    if from_text:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise PolicyError(name, f"'{name}' must be an integer") from None
    else:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise PolicyError(name, f"'{name}' must be an integer")
        value = raw
    if kind == "window":
        if value not in constraint:
            raise PolicyError(name, f"'{name}' must be one of {list(constraint)} seconds")
    elif value < constraint:
        raise PolicyError(name, f"'{name}' must be at least {constraint}")
    return value


@dataclass
class _Bucket:
    """One token bucket: continuous refill at ``limit / window`` tokens per second."""

    tokens: float
    updated: float  # time.monotonic stamp of the last refill
    limit: int
    burst: int
    window: float


class BucketRegistry:
    """The process-wide ``(subject, scope)`` bucket map behind the usage panel and reset."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def consume(
        self,
        subject: str,
        scope: str,
        limit: int,
        *,
        burst: int | None = None,
        window: float = 60.0,
        now: float | None = None,
    ) -> tuple[bool, float]:
        """Take one token: ``(allowed, retry_after_seconds)``; retry is 0 when allowed.

        ``limit <= 0`` is unlimited (section 2): the call succeeds and tracks nothing, so
        unlimited subjects never appear in the panel. A refusal consumes no token.
        """
        if limit <= 0:
            return True, 0.0
        capacity = max(1, int(burst if burst is not None else limit))
        clock = time.monotonic() if now is None else now
        with self._lock:
            if len(self._buckets) > _SWEEP_AT:
                self._sweep()
            key = (subject, scope)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    tokens=float(capacity),
                    updated=clock,
                    limit=int(limit),
                    burst=capacity,
                    window=window,
                )
                self._buckets[key] = bucket
            else:
                rate = bucket.limit / bucket.window
                bucket.tokens = min(
                    float(bucket.burst), bucket.tokens + (clock - bucket.updated) * rate
                )
                # Policy edits apply on the next use (6.1: no restart).
                bucket.updated = clock
                bucket.limit = int(limit)
                bucket.burst = capacity
                bucket.window = window
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            rate = bucket.limit / bucket.window
            retry = math.ceil((1.0 - bucket.tokens) / rate)
            return False, float(max(1, retry))

    def usage(self) -> list[dict]:
        """Active buckets with usage above 0, sorted by usage ratio (6.4 panel order).

        ``used`` is tokens taken since the last full refill, ``limit`` the policy rate for
        the window, ``resets_in`` seconds until the bucket is full and leaves the panel.
        """
        with self._lock:
            rows: list[dict] = []
            for (subject, scope), bucket in self._buckets.items():
                used = bucket.burst - bucket.tokens
                if used <= 1e-9:
                    continue
                rows.append(
                    {
                        "subject": subject,
                        "scope": scope,
                        "used": round(used, 2),
                        "limit": bucket.limit,
                        "resets_in": int(math.ceil(used / (bucket.limit / bucket.window))),
                    }
                )
        rows.sort(key=lambda row: row["used"] / row["limit"], reverse=True)
        return rows

    def reset(
        self,
        subject: str | None = None,
        scope: str | None = None,
        *,
        all_: bool = False,
    ) -> int:
        """Drop matching buckets (or every bucket for ``all_``); returns how many went."""
        with self._lock:
            if all_:
                cleared = len(self._buckets)
                self._buckets.clear()
                return cleared
            keys = [
                key
                for key in self._buckets
                if (subject is None or key[0] == subject)
                and (scope is None or key[1] == scope)
            ]
            for key in keys:
                del self._buckets[key]
            return len(keys)

    def _sweep(self) -> None:
        """Drop fully refilled buckets; they carry no usage and can be rebuilt on demand."""
        for key, bucket in list(self._buckets.items()):
            if bucket.tokens >= bucket.burst - 1e-9:
                del self._buckets[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


#: Process-wide registry (rate-limiting 9: bucket state is memory only, one process).
REGISTRY = BucketRegistry()
