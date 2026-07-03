"""Lazy factories for the runner and transport.

Using lru_cache so definitions are loaded from disk only once (at first call),
not at import time. This avoids issues during Django startup and in tests.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_runner():
    from pathlib import Path

    from django.conf import settings
    from harel.dsl import definition_from_dsl_file

    from django_harel import DjangoRunner

    machines_cfg: dict[str, str] = settings.DJANGO_HAREL.get("MACHINES", {})
    definitions = {defn_id: definition_from_dsl_file(Path(path)) for defn_id, path in machines_cfg.items()}
    return DjangoRunner(definitions=definitions)


def get_transport():
    from django_harel.transport import DjangoTransport

    return DjangoTransport()
