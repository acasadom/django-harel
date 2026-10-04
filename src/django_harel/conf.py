"""django-harel's settings, the machines they name, and the runners built over them.

    DJANGO_HAREL = {
        "MACHINES": {"publishing": "articles/machines/publishing.stm"},
        "DATABASE": "default",
        "TRACE": False,
        "ON_ACTION_ERROR": "fail",
    }

A machine's path is relative to `settings.BASE_DIR` when the project defines it, else to the
working directory. Each process loads the machines and builds its runners once, on first use.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from harel.definition.model import Definition
from harel.engine.distributed import DistributedRunner
from harel.engine.durable import DurableRunner

DEFAULTS: dict[str, Any] = {
    "MACHINES": {},
    "DATABASE": "default",
    "TRACE": False,
    "ON_ACTION_ERROR": "fail",
    "VISIBILITY": 30.0,
    "SUSPEND_RECHECK": 5.0,
    "IDLE_SLEEP": 0.2,
    "ATOMIC_STEP": True,
}

_lock = threading.RLock()  # a runner loads the definitions while holding it
_cache: dict[str, Any] = {}


def get_setting(key: str) -> Any:
    """`DJANGO_HAREL[key]`, or its default."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown DJANGO_HAREL setting {key!r}")
    return getattr(settings, "DJANGO_HAREL", {}).get(key, DEFAULTS[key])


def _machine_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    base = getattr(settings, "BASE_DIR", None)
    return Path(base) / p if base is not None else p


def _cached(name: str, build: Any) -> Any:
    with _lock:
        if name not in _cache:
            _cache[name] = build()
        return _cache[name]


def _load_definitions() -> dict[str, Definition]:
    from harel.dsl import definition_from_dsl_file

    machines: dict[str, str] = get_setting("MACHINES")
    if not machines:
        raise ImproperlyConfigured("DJANGO_HAREL['MACHINES'] names no machine")
    definitions = {}
    for machine_id, path in machines.items():
        defn = definition_from_dsl_file(_machine_path(path), machine_id, validate=True)
        definitions[defn.id] = defn
    return definitions


def definitions() -> dict[str, Definition]:
    """The machines in `DJANGO_HAREL['MACHINES']`, by id, loaded and validated once."""
    return _cached("definitions", _load_definitions)


def durable_runner() -> DurableRunner:
    """The runner that drives an execution in the caller's thread, to the end of each event:
    its actions run in the request, inside its transaction. For flows whose state the request
    itself moves on (wizards, checkouts) — no queue, no worker."""
    from django_harel.store import DjangoStore

    return _cached(
        "durable",
        lambda: DurableRunner(
            DjangoStore(get_setting("DATABASE")),
            definitions(),
            trace=get_setting("TRACE"),
            execution="inline",
            on_action_error=get_setting("ON_ACTION_ERROR"),
        ),
    )


def distributed_runner() -> DistributedRunner:
    """The runner that hands events to the workers: `create`, `start` and `send` write the
    execution and its queued events in the caller's transaction, and `manage.py harel_worker`
    runs them — the actions never run in the request."""
    from django_harel.store import DjangoStore
    from django_harel.transport import DjangoTransport

    database = get_setting("DATABASE")
    return _cached(
        "distributed",
        lambda: DistributedRunner(
            DjangoStore(database),
            DjangoTransport(database),
            definitions(),
            trace=get_setting("TRACE"),
            execution="inline",
        ),
    )


def reset() -> None:
    """Forget the loaded machines and the runners (they're rebuilt on next use)."""
    with _lock:
        _cache.clear()


def _on_setting_changed(*, setting: str, **kwargs: Any) -> None:
    if setting in ("DJANGO_HAREL", "BASE_DIR"):
        reset()


setting_changed.connect(_on_setting_changed)
