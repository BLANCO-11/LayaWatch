"""Uvicorn serving path for the FastAPI app built by ``layawatch.app``.

The HTTP wire behavior (request limits, header normalization, gzip, SSE
framing) lives in the app bridge; this module only serves a ready-made
``FastAPI`` app on ``config.bind:config.port`` with ``Server: LayaWatch``
and logs the start/stop lines.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn

from layawatch.log import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from layawatch.config import Config

_logger = get_logger("http")


def run(config: Config, app: FastAPI) -> None:
    """Serve ``app`` on ``config.bind:config.port`` until SIGTERM/SIGINT."""
    _logger.info(f"serving on {config.bind}:{config.port}")
    try:
        uvicorn.run(
            app,
            host=config.bind,
            port=config.port,
            headers=[("server", "LayaWatch")],
            access_log=False,
        )
    finally:
        _logger.info("server stopped")
