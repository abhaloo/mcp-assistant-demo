"""Request-scoped access to the process's resource container.

The lifespan builds the container once and stores it on application state;
handlers reach it only through this dependency, so a request never looks up or
constructs a resource owner of its own.
"""

from __future__ import annotations

from fastapi import Request

from app.resources import ProcessResources


def get_resources(request: Request) -> ProcessResources:
    return request.app.state.resources
