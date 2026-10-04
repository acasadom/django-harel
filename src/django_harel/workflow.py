"""A model's machine, as an attribute of the model.

    class Article(models.Model):
        ...
        workflow = Workflow("publishing", autostart=True)

    article.workflow.state            # "InReview"
    article.workflow.send("Submit")   # queued for the workers, in the current transaction
    article.workflow.process("Pay", token=token)  # run now, in this thread and transaction

The execution belongs to the row: its id comes from the model, the attribute and the primary
key (`articles.article.workflow:42`), so the model needs no column for it. `instance_for(execution)`
goes the other way — the row an execution belongs to, e.g. in a signal receiver.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import class_prepared, post_save
from harel.engine.execution import Execution, Status
from harel.spec.states import Event

from django_harel import conf


class Workflow:
    """The machine `definition_id`, one execution per row of the model it is declared on.

    - `context(obj)` — the new execution's context; default `{"<model>_id": obj.pk}`.
    - `execution_id(obj)` — its id; default `"<app_label>.<model>.<attribute>:<pk>"`.
    - `autostart` — start it when a row is created, in the transaction that saves the row.
    - `run` — `"background"` (default): `start()` queues the `Start` for the workers;
      `"request"`: `start()` runs it now, in the caller.
    """

    def __init__(
        self,
        definition_id: str,
        *,
        context: Optional[Callable[[Any], dict]] = None,
        execution_id: Optional[Callable[[Any], str]] = None,
        autostart: bool = False,
        run: str = "background",
    ) -> None:
        if run not in ("background", "request"):
            raise ValueError(f"run must be 'background' or 'request', got {run!r}")
        self.definition_id = definition_id
        self.autostart = autostart
        self.run = run
        self._context = context
        self._execution_id = execution_id
        self.model: Optional[type[models.Model]] = None
        self.name = ""

    def contribute_to_class(self, cls: type[models.Model], name: str) -> None:
        """Django's hook for a model attribute, called once the model has its `_meta`."""
        self.model, self.name = cls, name
        setattr(cls, name, self)
        if not self.autostart:
            return
        if cls._meta.abstract:
            # a concrete model inheriting it gets the attribute, not this call: connect it then
            class_prepared.connect(self._on_class_prepared, weak=False)
        else:
            self._connect(cls)

    def _on_class_prepared(self, sender: type[models.Model], **kwargs: Any) -> None:
        if self.model is not None and issubclass(sender, self.model) and not sender._meta.abstract:
            self._connect(sender)

    def _connect(self, model: type[models.Model]) -> None:
        post_save.connect(
            self._on_created,
            sender=model,
            weak=False,
            dispatch_uid=f"django_harel:{model._meta.label}.{self.name}",
        )

    def __get__(self, obj: Any, owner: type) -> Any:
        if obj is None:
            return self
        return BoundWorkflow(self, obj)

    def _on_created(
        self, sender: type, instance: Any, created: bool, raw: bool = False, **kwargs: Any
    ) -> None:
        if created and not raw:  # not for fixtures loaded with loaddata
            BoundWorkflow(self, instance).start()

    def execution_id_for(self, obj: Any) -> str:
        if obj.pk is None:
            raise ValueError(f"{obj!r} has no primary key yet: save it before using its workflow")
        if self._execution_id is not None:
            return self._execution_id(obj)
        return f"{obj._meta.label_lower}.{self.name}:{obj.pk}"

    def context_for(self, obj: Any) -> dict:
        if self._context is not None:
            return dict(self._context(obj))
        return {f"{obj._meta.model_name}_id": obj.pk}


class BoundWorkflow:
    """A workflow for one row: its execution, and the ways to move it on."""

    def __init__(self, workflow: Workflow, obj: Any) -> None:
        self.workflow = workflow
        self.obj = obj

    def __repr__(self) -> str:
        return f"<BoundWorkflow {self.id}>"

    @property
    def id(self) -> str:
        """The execution's id."""
        return self.workflow.execution_id_for(self.obj)

    # --- where it is -----------------------------------------------------------------------------
    @property
    def execution(self) -> Optional[Execution]:
        """The execution as stored now (None before it is started); read fresh on each access."""
        return conf.durable_runner().store.load(self.id)

    @property
    def exists(self) -> bool:
        return self.execution is not None

    @property
    def status(self) -> Optional[Status]:
        exe = self.execution
        return exe.status if exe is not None else None

    @property
    def state(self) -> Optional[str]:
        """The active state's path (`"InReview"`, `"Checkout.Payment"`), None before it starts."""
        exe = self.execution
        return exe.active_path if exe is not None else None

    @property
    def outcome(self) -> Optional[str]:
        exe = self.execution
        return exe.outcome if exe is not None else None

    # --- moving it on ----------------------------------------------------------------------------
    def start(self, *, priority: int = 0, **context: Any) -> Execution:
        """Create the execution, with the workflow's context plus `context`: queued for the
        workers, or run now with `run="request"`. Raises `ExecutionAlreadyExists` if there is one."""
        ctx = {**self.workflow.context_for(self.obj), **context}
        if self.workflow.run == "request":
            return conf.durable_runner().create(self.workflow.definition_id, ctx, self.id, priority)
        return conf.distributed_runner().create(self.workflow.definition_id, ctx, self.id, priority)

    def send(self, kind: str, *, event_id: Optional[str] = None, **data: Any) -> None:
        """Queue the event `kind` (with `data`) for the workers, in the current transaction.
        `event_id` dedupes it: a second event with the same id is ignored."""
        conf.distributed_runner().send(self.id, _event(kind, event_id, data))

    def process(self, kind: str, *, event_id: Optional[str] = None, **data: Any) -> Execution:
        """Run the event `kind` (with `data`) now, in this thread and transaction; return the
        execution as it ends up."""
        return conf.durable_runner().process(self.id, _event(kind, event_id, data))


def _event(kind: str, event_id: Optional[str], data: dict) -> Event:
    return Event(kind=kind, data=data, **({"id": event_id} if event_id is not None else {}))


def instance_for(execution: Execution) -> Optional[models.Model]:
    """The row `execution` belongs to, for an execution a `Workflow` started with its default
    id; None for any other (a child execution, or an id the workflow was told to make another
    way)."""
    if execution.parent_id is not None or ":" not in execution.id:
        return None
    owner, pk = execution.id.split(":", 1)
    label, _, name = owner.rpartition(".")
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        return None
    workflow = getattr(model, name, None)
    if not isinstance(workflow, Workflow) or workflow._execution_id is not None:
        return None
    if workflow.definition_id != execution.definition_id:
        return None
    try:
        return model._default_manager.get(pk=model._meta.pk.to_python(pk))
    except (model.DoesNotExist, ValidationError, ValueError, TypeError):
        return None
