"""
HTTP status server — serves scripts/status.html at GET /botsebolstatus.
Designed to run in a background thread alongside Slack Bolt Socket Mode.

Usage:
    from scripts.status_server import start_status_server_thread
    start_status_server_thread()  # call before handler.start()
"""
import asyncio
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).parent / "status.html"
_PORT = int(os.environ.get("STATUS_PORT", 8080))


async def _run_server():
    from aiohttp import web

    async def handle_status(request):
        try:
            html = _HTML_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            html = "<h1>Sebolek — running</h1>"
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    app = web.Application()
    app.router.add_get("/botsebolstatus", handle_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", _PORT)
    await site.start()
    logger.info("Status server listening on port %d at /botsebolstatus", _PORT)
    # Run forever
    while True:
        await asyncio.sleep(3600)


def _thread_target():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_server())
    except Exception as exc:
        logger.error("Status server crashed: %s", exc)


def start_status_server_thread():
    """Start the aiohttp status server in a daemon thread."""
    t = threading.Thread(target=_thread_target, daemon=True, name="status-server")
    t.start()
    return t
