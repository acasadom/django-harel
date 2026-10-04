# Load test

`run.py` pushes N executions of `pipeline.stm` through W `manage.py harel_worker` processes and
checks the result: every execution done, every action run exactly once (a unique key per
execution and step makes a second run fail), and the queue and the outbox empty.

```bash
# SQLite (bench/bench.db)
uv run python bench/run.py --executions 2000 --workers 4

# a server, started as for the tests
HAREL_TEST_DB=postgres uv run python bench/run.py --executions 10000 --workers 8 --prefill
HAREL_TEST_DB=mysql    uv run python bench/run.py --executions 10000 --workers 8 --prefill
```

Without `--prefill` the executions are created and sent their `Go` while the workers run, so
publishes race the workers' acks. With it, everything is queued first and the time measures
the workers alone. Each execution is two events (`Start`, `Go`) and three actions.

The worker logs are in `bench/logs/`.
