# Admin

With `django.contrib.admin` installed, **Harel › Executions** lists every execution, and its
**Dashboard** gives one row per machine.

## The dashboard

**Executions › Dashboard** is the one-screen operator view — what you check first, and what
you'd watch after a deploy. One row per machine `conf.definitions()` knows: every entry in
`DJANGO_HAREL["MACHINES"]`, and the submachines they `invoke` (a fan-out's parcel, say — it
runs as its own executions, under its own id).

Each row has:

- its executions **by status**, each count linked to the filtered list;
- its **failed** executions, linked (up to 10, with how many more);
- its **queue**: how many events are waiting, and how long the oldest has waited;
- its **timers**: how many are overdue — fired over a minute ago and still not delivered.

A nonempty queue that never shrinks, or an overdue-timers count that only grows, both say the
same thing: **no worker is running** for that machine (or it's stuck). That's the single most
useful thing this page tells you, and it needs nothing instrumented — it reads the same tables
`manage.py harel_worker` does.

## The list

Each row shows the execution's id, machine, status, outcome, current state, when it last
changed and its version. Filter by status, by machine, or to root executions only (no
orthogonal regions or invoked children); search by id.

## The detail page

Everything about one execution, in sections:

- **Status, outcome, state** and the error that dead-lettered it, with its timestamps.
- **Machine** — the machine's diagram, and the states the execution is in: inside an
  orthogonal state, the one each region is in.
- **Parent and children** — its orthogonal regions and invoked machines, each linked, with
  where it is; and its parent, for a child.
- **Waiting** — the events in its queue, oldest first, with the worker holding the one in
  hand and until when; and its armed timers. A timer due over a minute ago and still not fired
  is flagged: no worker is sweeping the timers.
- **Timeline** — its steps, latest first: the event, the transition, the actions it ran and
  what a `set` wrote. Recorded only with `DJANGO_HAREL["TRACE"] = True`.
- **Context**, as JSON.

Nothing here can be edited: only the machine changes an execution.

The diagram is drawn in the browser by [Mermaid](https://mermaid.js.org), loaded from
`DJANGO_HAREL["MERMAID_JS"]` — a CDN by default. For an admin without internet access, serve a
copy of `mermaid.min.js` with your static files and point the setting at it. Without Mermaid
the diagram's source shows as text.

## Actions

| Action | |
|---|---|
| Suspend / Resume | pause an execution and continue it |
| Cancel | run the machine's `on Cancel` cleanup (on a worker), or terminate |
| Terminate | end it now, without cleanup |
| Redrive | bring `FAILED` executions back to `RUNNING`, at a state you pick (below) |
| Purge | delete finished executions and their children; one still running is refused |

## Redrive

An execution an action dead-lettered (`FAILED`) waits for someone to fix what failed it. Once
that's done, select it — or several of one machine — and run **Redrive**: a page lists them
with their errors, and asks for the state to resume at. It offers only the states harel can
resume them at — the leaves of their own branch, outside any orthogonal state — and no
terminal; the one they failed in comes preselected. The execution's context is left as it is,
and the machine carries on from that state with its next event.

## Permissions

They are offered to users with the `django_harel.change_harelexecution` permission; the
`view_harelexecution` permission shows the pages without them.
