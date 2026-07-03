from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_harel_setting(key: str, default: Any = None) -> Any:
    """Read a value from settings.DJANGO_HAREL (dict). Falls back to `default`."""
    cfg = getattr(settings, "DJANGO_HAREL", {})
    return cfg.get(key, default)


def build_transport():
    """Instantiate the configured Transport backend.

    Reads DJANGO_HAREL["TRANSPORT"] (default: "django"). Supported values:

    ``"django"`` (default)
        DjangoTransport — queue stored in the ``harel_queue`` table.
        Zero extra dependencies; works with any Django-supported database.

    ``"redis"``
        harel's RedisTransport. Requires ``redis`` (pip install redis) and
        DJANGO_HAREL["TRANSPORT_URL"] = "redis://..."

    Returns a sync Transport compatible with AsyncDjangoTransport (wrapped
    via sync_to_async in the worker) or usable directly in DjangoRunner.
    """
    backend = get_harel_setting("TRANSPORT", "django")

    if backend == "django":
        from django_harel.transport import DjangoTransport

        return DjangoTransport()

    if backend == "redis":
        try:
            from harel.engine.transport.redis import RedisTransport
        except ImportError as exc:
            raise ImproperlyConfigured(
                "DJANGO_HAREL TRANSPORT='redis' requires the redis package. "
                "Install it with: pip install redis"
            ) from exc
        url = get_harel_setting("TRANSPORT_URL", None)
        if not url:
            raise ImproperlyConfigured(
                "DJANGO_HAREL TRANSPORT='redis' requires TRANSPORT_URL (e.g. 'redis://localhost:6379')."
            )
        return RedisTransport.from_url(url)

    raise ImproperlyConfigured(
        f"Unknown DJANGO_HAREL TRANSPORT: {backend!r}. Valid values: 'django', 'redis'."
    )
