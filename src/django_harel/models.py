from django.db import models


class HarelExecution(models.Model):
    """Persisted snapshot of a running statechart Execution.

    `data` holds the full Execution JSON (Execution.model_dump_json()). The
    denormalised columns (`status`, `active_path`, `parent_id`) mirror fields
    inside `data` so admin list views can filter/sort without JSON extraction.
    They are updated atomically on every `DjangoStore.commit()`."""

    id = models.CharField(max_length=64, primary_key=True)
    definition_id = models.CharField(max_length=255, db_index=True)
    data = models.TextField()
    version = models.BigIntegerField(default=0)
    # denormalised for fast admin/list queries
    status = models.CharField(max_length=32, db_index=True, default="")
    active_path = models.CharField(max_length=255, blank=True, default="")
    parent_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    class Meta:
        app_label = "django_harel"
        db_table = "harel_executions"


class HarelOutbox(models.Model):
    """Transactional outbox: events committed alongside an Execution advance,
    delivered after the commit (at-least-once, outbox pattern)."""

    seq = models.BigAutoField(primary_key=True)
    target_id = models.CharField(max_length=64, null=True, blank=True)
    event = models.TextField()

    class Meta:
        app_label = "django_harel"
        db_table = "harel_outbox"


class HarelProcessedEvent(models.Model):
    """Deduplication table: records which (execution_id, event_id) pairs have
    already been processed to make event delivery idempotent."""

    execution_id = models.CharField(max_length=64)
    event_id = models.CharField(max_length=128)

    class Meta:
        app_label = "django_harel"
        db_table = "harel_processed_events"
        unique_together = [("execution_id", "event_id")]


class HarelTimer(models.Model):
    """Durable timers: keyed by (execution_id, path); fire_at is a Unix timestamp.
    The sweep delivers a Timeout event when fire_at <= now."""

    execution_id = models.CharField(max_length=64)
    path = models.CharField(max_length=255)
    fire_at = models.FloatField()

    class Meta:
        app_label = "django_harel"
        db_table = "harel_timers"
        unique_together = [("execution_id", "path")]


class HarelSpawn(models.Model):
    """Pending orthogonal child-Execution creations committed atomically with
    the parent's advance. A relay creates the child idempotently afterwards."""

    seq = models.BigAutoField(primary_key=True)
    parent_id = models.CharField(max_length=64)
    child_id = models.CharField(max_length=64)
    root_path = models.CharField(max_length=255)
    context = models.TextField()  # json.dumps(dict)

    class Meta:
        app_label = "django_harel"
        db_table = "harel_spawns"


class HarelTrace(models.Model):
    """Opt-in per-execution timeline: one row per event processed, capped to
    trace_max entries (ring buffer — oldest rows are pruned on commit)."""

    execution_id = models.CharField(max_length=64)
    idx = models.IntegerField()
    entry = models.TextField()

    class Meta:
        app_label = "django_harel"
        db_table = "harel_trace"
        unique_together = [("execution_id", "idx")]


class HarelQueue(models.Model):
    """DjangoTransport backing table: each row is a queued event for a group
    (group_id = execution_id). Claim locks a group; ack removes the head."""

    seq = models.BigAutoField(primary_key=True)
    group_id = models.CharField(max_length=64, db_index=True)
    payload = models.TextField()
    available_at = models.FloatField(default=0.0, db_index=True)
    locked_by = models.CharField(max_length=64, null=True, blank=True)
    lock_expiry = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "django_harel"
        db_table = "harel_queue"
