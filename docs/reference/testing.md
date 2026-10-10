# Testing

## Flushing harel's tables

`TestCase` rolls each test back, harel's tables included. `TransactionTestCase` (and
`pytest.mark.django_db(transaction=True)`) instead *flushes* the database after each test —
and Django's flush only empties tables a managed model owns, which harel's aren't. Empty them
yourself:

```python
from django.db import connection
from django_harel.schema import ALL_TABLES


def empty_harel_tables():
    with connection.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(f"DELETE FROM {table}")
```

With pytest, as an autouse fixture that runs it after each test; with `TransactionTestCase`,
from `tearDown`.

## Running the worker in a test

Drive a worker by hand instead of running the command — one event per `step()`:

```python
from django_harel import distributed_runner

runner = distributed_runner()
exe = runner.create("publishing", context={"article_id": article.pk})

worker = runner.worker()
while worker.step():
    pass
```

For timers, give the runner a clock you control and call `worker.fire_due_timers()`:

```python
from harel.engine.distributed import DistributedRunner
from django_harel import DjangoStore, DjangoTransport, definitions

clock = FakeClock()
runner = DistributedRunner(
    DjangoStore(), DjangoTransport(clock=clock), definitions(), clock=clock, execution="inline"
)
worker = runner.worker(clock=clock)
...
clock.now += 601
worker.fire_due_timers()
worker.step()
```
