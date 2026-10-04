"""Django signals for what harel commits, sent once the transaction holding the commit commits.

    from django.dispatch import receiver
    from django_harel.signals import execution_failed

    @receiver(execution_failed)
    def page_someone(sender, step, execution, **kwargs):
        ...

Each is sent with `sender=HarelExecution`, a `step` (`harel.engine.store.base.Step`: what caused
the commit, the status and state before and after, the event, the actions) and the `execution`
as committed. A step rolled back — a view that fails after advancing a machine, a worker step
that lost to another writer — sends nothing. A receiver that raises is logged and doesn't
undo anything: the step is committed.
"""

from __future__ import annotations

import logging

from django.dispatch import Signal
from harel.engine.execution import Execution, Status
from harel.engine.store.base import Step

logger = logging.getLogger("django_harel.signals")

#: every committed step
step_committed = Signal()
#: a step that took an execution to DONE or CANCELLED
execution_finished = Signal()
#: a step that took an execution to FAILED (an action raised and no `on error` took it)
execution_failed = Signal()

_FINISHED = (Status.DONE, Status.CANCELLED)


def listened() -> bool:
    """Whether any receiver is connected (else a commit skips preparing the signals)."""
    return (
        step_committed.has_listeners()
        or execution_finished.has_listeners()
        or execution_failed.has_listeners()
    )


def send(step: Step, execution: Execution) -> None:
    from django_harel.models import HarelExecution

    sent = [step_committed]
    if step.to_status in _FINISHED and step.from_status not in _FINISHED:
        sent.append(execution_finished)
    elif step.to_status is Status.FAILED and step.from_status is not Status.FAILED:
        sent.append(execution_failed)
    for signal in sent:
        for receiver, error in signal.send_robust(sender=HarelExecution, step=step, execution=execution):
            if isinstance(error, Exception):
                logger.error(
                    "a receiver of a harel signal raised for execution %s; the step is committed",
                    execution.id,
                    exc_info=error,
                )
