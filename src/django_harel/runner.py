"""DjangoRunner — a synchronous, in-thread statechart runner for Django views.

Unlike `DurableRunner` (which bridges to an async event loop via anyio's
BlockingPortal), DjangoRunner runs the harel engine synchronously in the
current thread. This is the correct choice for Django views and API endpoints:
the runner participates in `transaction.atomic()` — advancing the statechart
and saving domain models happen in a single transaction.

For background work (long-running actions, retries, fan-out) use the
`harel_worker` management command instead — it runs an async worker over
`AsyncDjangoStore`.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from harel.definition.model import Definition
from harel.engine.execution import Execution
from harel.engine.resolve import MachineResolver
from harel.engine.runtime import _SyncDriver
from harel.spec.states import Event

from django_harel.store import DjangoStore


class DjangoRunner:
    """Synchronous statechart runner for Django views and request handlers.

    Wraps harel's `_SyncDriver` with a `DjangoStore`, running everything in
    the current thread so the caller's `transaction.atomic()` context is shared.

    Usage::

        runner = DjangoRunner(definitions={"checkout": defn}, actions=ACTIONS)

        # Create and start a new execution:
        exe = runner.create("checkout", context={"user_id": user.pk})

        # Advance it on a subsequent request:
        exe = runner.process(exe.id, Event("PaymentReceived"))

        # Inside a view with a domain model:
        with transaction.atomic():
            order = Order.objects.create(user=user)
            exe = runner.process(exe.id, Event("OrderCreated", data={"order_id": order.pk}))
            # Both commit together — or both roll back.
    """

    def __init__(
        self,
        definitions: dict[str, Definition],
        store: Optional[DjangoStore] = None,
        clock: Callable[[], float] = time.time,
        resolver: Optional[MachineResolver] = None,
        trace: bool = False,
    ) -> None:
        if not definitions:
            raise ValueError("DjangoRunner requires at least one Definition")
        self.definitions = dict(definitions)
        # Include submachines so invoke targets resolve without extra registration
        for defn in list(definitions.values()):
            self.definitions.update({s.id: s for s in defn.submachines.values()})
        self.store = store if store is not None else DjangoStore()
        self._clock = clock
        self._resolver = resolver
        self._trace = trace

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_machine(self, fqn: str) -> Definition:
        if fqn in self.definitions:
            return self.definitions[fqn]
        if self._resolver is None:
            from harel.engine.resolve import ResolveError

            raise ResolveError(f"invoke {fqn!r} but DjangoRunner has no machine resolver")
        defn = self._resolver.resolve(fqn)
        self.definitions[defn.id] = defn
        return defn

    def _driver(self, definition_id: str) -> _SyncDriver:
        return _SyncDriver(
            defn=self.definitions[definition_id],
            store=self.store,
            clock=self._clock,
            definitions=self.definitions,
            resolve_machine=self._resolve_machine,
            trace=self._trace,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, definition_id: str, context: Optional[dict] = None) -> Execution:
        """Create, start, and persist a new Execution; return its committed state."""
        exe = Execution(definition_id=definition_id, context=dict(context or {}))
        self._driver(definition_id).start(exe)
        loaded = self.store.load(exe.id)
        assert loaded is not None, f"execution {exe.id!r} missing after create"
        return loaded

    def process(self, execution_id: str, event: Event) -> Execution:
        """Load a persisted Execution, feed it one event, return the committed state."""
        exe = self.store.load(execution_id)
        if exe is None:
            raise KeyError(execution_id)
        self._driver(exe.definition_id).inject(exe, event)
        loaded = self.store.load(execution_id)
        assert loaded is not None, f"execution {execution_id!r} missing after process"
        return loaded

    def recover(self, definition_id: str) -> None:
        """Drain the durable outbox for `definition_id` (relay on restart)."""
        self._driver(definition_id).recover()

    def fire_due_timers(self) -> int:
        """Sweep and deliver all timers due now. Returns count fired."""
        fired = 0
        for definition_id in self.definitions:
            fired += self._driver(definition_id).fire_due_timers()
        return fired
