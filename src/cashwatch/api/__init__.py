"""HTTP API (agent A): ``create_app(settings, store=None)``; entrypoint ``cashwatch.api.main:run``."""

from cashwatch.api.app import create_app

__all__ = ["create_app"]
