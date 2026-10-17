"""API entrypoint: single uvicorn process, settings from CASHWATCH_* environment.

Refuses to start when ``settings.production_errors()`` is non-empty or the API key secret
file is unreadable/weak. The key value is never printed or logged.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from cashwatch.settings import get_settings


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    errors = settings.production_errors()
    if errors:
        for e in errors:
            print(f"cashwatch-api: startup refused: {e}", file=sys.stderr)
        raise SystemExit(2)
    from cashwatch.api.app import create_app

    try:
        app = create_app(settings)
    except (OSError, RuntimeError) as e:
        # Message is generic (no key material, no absolute path).
        print(f"cashwatch-api: startup refused: {type(e).__name__}: API key / storage unavailable",
              file=sys.stderr)
        raise SystemExit(2) from None
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, access_log=False,
                server_header=False, date_header=True, proxy_headers=False, timeout_keep_alive=5,
                log_config=None)


if __name__ == "__main__":
    run()
