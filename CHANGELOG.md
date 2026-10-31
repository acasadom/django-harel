# Changelog

All notable changes to django-harel. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: a minor version may break the API).

## Unreleased

The first release. It requires Python 3.11+, Django 5.2+ and harel 0.10+.

### Added

- **Storage on Django's database.** `DjangoStore` and `DjangoTransport` implement harel's
  `ExecutionStore` and `Transport` protocols over Django's own connection, so an execution's
  state, queue and timers join the caller's transaction. They pass harel's contract suites
  (`harel.testing`).
- **SQLite, PostgreSQL and MySQL/MariaDB.** One migration creates harel's schema version 6. On
  SQLite and PostgreSQL the tables are harel's own, so harel's native backends and tools can
  read them.
- **`ExecutionField`.** It ties a machine to a model, as an attribute named after the machine:
  `article.publishing.send("Submit")`, `.state`, `.process(...)` in the request. With
  `autostart=True`, the execution starts with the row.
- **Runners.** `distributed_runner()` queues events for a worker. `durable_runner()` runs them in
  the request. `definitions()` loads the machines in `DJANGO_HAREL["MACHINES"]`.
- **`manage.py harel_worker`.** Each event runs in one transaction: its actions' writes, the
  machine's step and the acknowledgement commit together.
  - `--machines` dedicates a worker to some machines.
  - At startup the worker publishes the machine versions it runs.
  - It records a heartbeat every `HEARTBEAT` seconds and removes it when it stops.
- **Signals.** `step_committed`, `execution_finished` and `execution_failed` are sent once the
  step's transaction has committed.
- **The admin.**
  - **Executions**: each execution's diagram with its active states, its tree of regions and
    children, what it waits for, and its timeline.
  - **Actions** on the executions list: suspend, resume, cancel, terminate, purge, and redrive
    failed executions at a chosen state.
  - **Machines**: every machine with its version, diagram and top-level executions; counts by
    status; overdue timers; the queue by machine; and every worker's heartbeat. It flags work
    no live worker claims, and a gone worker can be forgotten.
- **Testing helpers.** `ALL_TABLES`, to empty harel's tables between `TransactionTestCase` tests.
- **An example app** (`example/`): articles and orders, with a REST API, a worker and demo data,
  on PostgreSQL with `docker compose up`.
