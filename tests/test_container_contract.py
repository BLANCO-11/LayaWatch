"""Static container contract (plan phase-8 task 3): the Dockerfile shape and the compose
hardening asserted without building the image, so regressions fail in CI's pytest job.

Asserts what the release acceptance depends on: the two stage names, the pinned base images,
non-root uid 10001, the HEALTHCHECK, and that no Node toolchain leaks into the runtime stage;
for compose: read-only root filesystem, dropped capabilities, healthcheck, state/model volumes
and restart policy.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _dockerfile() -> str:
    return (REPO / "Dockerfile").read_text()


def _compose() -> str:
    return (REPO / "docker-compose.yml").read_text()


def test_build_stage_is_node_and_exports_web_out() -> None:
    text = _dockerfile()
    assert "FROM node:24-slim AS web" in text
    build_stage = text.split("FROM python:3.12-slim")[0]
    assert "npm ci" in build_stage
    assert "npm run build" in build_stage


def test_runtime_stage_is_pinned_python_non_root_and_healthchecked() -> None:
    text = _dockerfile()
    assert "FROM python:3.12-slim" in text
    assert "LAYA_STATE_DIR=/data" in text
    assert "LAYWATCH_BIND=0.0.0.0" in text
    assert "--uid 10001" in text
    assert "USER layawatch" in text
    assert "HEALTHCHECK" in text
    assert "/healthz" in text
    assert 'CMD ["python", "-m", "layawatch"]' in text


def test_runtime_stage_has_no_node_toolchain() -> None:
    _first, runtime = _dockerfile().split("FROM python:3.12-slim", 1)
    assert "node" not in runtime.lower().split("\n")[0]  # stage name never aliases node
    for banned in ("npm ", "npm\t", "FROM node", "node_modules"):
        assert banned not in runtime, banned


def test_runtime_stage_ships_the_password_deny_list() -> None:
    # auth/passwords.py reads data/common_passwords.txt from parents[2]; in the image
    # that is site-packages, so the Dockerfile must place it there.
    assert "common_passwords.txt" in _dockerfile()


def test_wheel_declares_sql_package_data() -> None:
    # pip install ships only .py files unless package-data lists the SQL assets; without
    # them the image boots with zero migrations and crashes on "no such table: settings".
    text = (REPO / "pyproject.toml").read_text()
    assert "[tool.setuptools.package-data]" in text
    assert "store/migrations/*.sql" in text
    assert "store/schema.sql" in text


def test_compose_hardens_the_service() -> None:
    text = _compose()
    assert "read_only: true" in text
    assert "- ALL" in text  # cap_drop: ALL
    assert "no-new-privileges" in text
    assert "restart: unless-stopped" in text
    assert "/healthz" in text
    assert "layawatch-data:/data" in text
    assert "layawatch-models:/models" in text
    assert "LAYA_ADMIN_TOKEN" not in text
