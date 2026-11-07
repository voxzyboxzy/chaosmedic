"""ASGI entrypoint."""

from chaosmedic.api import create_app

app = create_app()
