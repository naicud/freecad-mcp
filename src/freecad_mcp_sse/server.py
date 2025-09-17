from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastmcp.server.http import create_sse_app

from freecad_mcp import server as base_server

LOGGER = logging.getLogger("FreeCADMCPserver.sse")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_SSE_PATH = "/sse"
DEFAULT_MESSAGE_PATH = "/messages"


def _normalize_relative_path(path: str) -> str:
    """Ensure the provided path is a relative HTTP path."""
    stripped = path.strip()
    if not stripped:
        raise ValueError("Path cannot be empty")
    if "://" in stripped or stripped.startswith("//"):
        raise ValueError("Path must be relative and must not include a scheme or network location")
    if "?" in stripped or "#" in stripped:
        raise ValueError("Path must not contain query strings or fragments")
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    return stripped


def _set_only_text_feedback(enabled: bool) -> None:
    """Propagate the text-only feedback setting to the base server module."""
    setattr(base_server, "_only_text_feedback", enabled)
    base_server.logger.info("Only text feedback: %s", enabled)


def create_app(
    *,
    only_text_feedback: bool = False,
    sse_path: str = DEFAULT_SSE_PATH,
    message_path: str = DEFAULT_MESSAGE_PATH,
    debug: bool = False,
) -> FastAPI:
    """Create a FastAPI application exposing the FastMCP server over SSE."""
    normalized_sse_path = _normalize_relative_path(sse_path)
    normalized_message_path = _normalize_relative_path(message_path)

    _set_only_text_feedback(only_text_feedback)

    sse_app = create_sse_app(
        server=base_server.mcp,
        message_path=normalized_message_path,
        sse_path=normalized_sse_path,
        debug=debug,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with sse_app.router.lifespan_context(sse_app):
            yield

    app = FastAPI(lifespan=lifespan, debug=debug)
    app.mount("/", sse_app)
    app.state.fastmcp_server = base_server.mcp
    app.state.sse_path = normalized_sse_path
    app.state.message_path = normalized_message_path

    return app


def main() -> None:
    """Run the SSE server using FastAPI and uvicorn."""
    parser = argparse.ArgumentParser(description="Run the FreeCAD MCP SSE server")
    parser.add_argument(
        "--only-text-feedback",
        action="store_true",
        help="Disable screenshot feedback and respond with text only",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host interface for the uvicorn server")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="TCP port for the uvicorn server",
    )
    parser.add_argument(
        "--sse-path",
        default=DEFAULT_SSE_PATH,
        help="Relative path that clients use to establish SSE connections",
    )
    parser.add_argument(
        "--message-path",
        default=DEFAULT_MESSAGE_PATH,
        help="Relative path where clients POST MCP messages",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Log level forwarded to uvicorn",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable FastAPI debug mode for additional diagnostics",
    )
    args = parser.parse_args()

    base_server._maybe_enable_debugpy()

    try:
        app = create_app(
            only_text_feedback=args.only_text_feedback,
            sse_path=args.sse_path,
            message_path=args.message_path,
            debug=args.debug,
        )
    except ValueError as exc:  # pragma: no cover - defensive guard around CLI usage
        parser.error(str(exc))
        return

    LOGGER.info(
        "Starting FreeCAD MCP SSE server at %s:%s (SSE path %s, message path %s)",
        args.host,
        args.port,
        app.state.sse_path,
        app.state.message_path,
    )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
