"""Login failure counting and exponential backoff (plan phase-3 task 7, rate-limiting 2).

Failures are counted per ``ip + email`` subject in memory (rate-limiting section 9: bucket
state never touches SQLite). The first ``limit`` failures inside the window only record
``auth.login_failed``; a subject carrying ``limit`` or more failures inside the window is
refused with ``429`` before any password work, audited ``auth.login_blocked``, and answered
with an increasing ``Retry-After`` of ``2 ** (failures - limit)`` seconds capped at the
window (15 minutes by default, so 2^10 and beyond all clamp to the same ceiling). The
counter clears on a successful login or when an owner edits the account, entries age out
of the window on access, and a sweep drops wholly-aged subjects when the map grows, so a
lockout can never outlive ``LAYWATCH_RATELIMIT_LOGIN_WINDOW`` and an attacker minting
subjects cannot grow the map without bound.

Clock is ``time.monotonic`` (rate-limiting section 2).
"""
from __future__ import annotations

import math
import threading
import time

#: Subjects kept before a sweep drops every wholly-aged one.
_SWEEP_AT = 1024


class LoginBackoff:
    """Per-subject login failure window with exponential refusal backoff."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self._lock = threading.Lock()
        # subject -> list of monotonic failure timestamps (pruned on access).
        self._failures: dict[tuple[str, str], list[float]] = {}

    def refused(self, ip: str, email: str, *, now: float | None = None) -> int | None:
        """Refusal in seconds when the subject is over the limit right now, else None.

        Counts the refusal itself (so consecutive 429s grow the wait) and caps the answer
        at the moment the oldest failure ages out of the window, so Retry-After always
        names the real unblock time.
        """
        subject = (ip, email)
        current = time.monotonic() if now is None else now
        with self._lock:
            failures = self._prune(subject, current)
            if len(failures) < self.limit:
                return None
            failures.append(current)
            self._sweep(current)
            wait = min(2 ** (len(failures) - self.limit), self.window)
            window_left = (failures[0] + self.window) - current
            return max(1, math.ceil(min(wait, window_left)))

    def record_failure(self, ip: str, email: str, *, now: float | None = None) -> int:
        """Count one wrong-password attempt; returns the failure count inside the window."""
        subject = (ip, email)
        current = time.monotonic() if now is None else now
        with self._lock:
            failures = self._prune(subject, current)
            failures.append(current)
            self._sweep(current)
            return len(failures)

    def clear(self, ip: str, email: str) -> None:
        """Forget one subject (a successful login resets its backoff)."""
        with self._lock:
            self._failures.pop((ip, email), None)

    def clear_email(self, email: str) -> int:
        """Forget every subject for one email (an owner action resets the backoff)."""
        cleared = 0
        with self._lock:
            for subject in [s for s in self._failures if s[1] == email]:
                del self._failures[subject]
                cleared += 1
        return cleared

    def failures(self, ip: str, email: str, *, now: float | None = None) -> int:
        """Current in-window failure count (diagnostics and tests)."""
        current = time.monotonic() if now is None else now
        with self._lock:
            return len(self._prune((ip, email), current))

    def _prune(self, subject: tuple[str, str], now: float) -> list[float]:
        failures = self._failures.setdefault(subject, [])
        cutoff = now - self.window
        if failures and failures[0] <= cutoff:
            failures = [ts for ts in failures if ts > cutoff]
            self._failures[subject] = failures
        return failures

    def _sweep(self, now: float) -> None:
        if len(self._failures) <= _SWEEP_AT:
            return
        cutoff = now - self.window
        stale = [s for s, ts in self._failures.items() if not ts or ts[-1] <= cutoff]
        for subject in stale:
            del self._failures[subject]


#: Process-wide backoff, configured once at route registration from
#: ``LAYWATCH_RATELIMIT_LOGIN`` / ``LAYWATCH_RATELIMIT_LOGIN_WINDOW``. Owner edits reset
#: subjects through :func:`clear_email` without importing the API layer.
BACKOFF = LoginBackoff(limit=5, window=900)


def configure(limit: int, window: int) -> None:
    """Point the process-wide backoff at the configured limit and window (startup only)."""
    BACKOFF.limit = max(1, int(limit))
    BACKOFF.window = float(window)


def clear_email(email: str) -> int:
    """Owner-action reset: forget every subject for one email; returns the count."""
    return BACKOFF.clear_email(email)
