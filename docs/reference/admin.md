# Admin

With `django.contrib.admin` installed, **Harel Statecharts › Executions** is where you look at
what's running. It's two kinds of page:

- **Machines** — one page per machine, for understanding its shape and whether it's healthy.
- **An execution's own page** — everything about one execution, its whole tree included.

## Machines

**Executions › Machines** lists every machine `conf.definitions()` knows: every entry in
`DJANGO_HAREL["MACHINES"]`, and the submachines they `invoke` (a fan-out's parcel, say — it runs
as its own executions, under its own id, so it gets its own row here too). Each name links to
the machine's own page.

Each row has:

- its executions **by status**, each count linked to the filtered list;
- its **failed** executions, linked (up to 10, with how many more);
- its **queue**: how many events are waiting, and how long the oldest has waited;
- its **timers**: how many are overdue — fired over a minute ago and still not delivered.

A nonempty queue that never shrinks, or an overdue-timers count that only grows, both say the
same thing: **no worker is running** for that machine (or it's stuck). That's the single most
useful thing this page tells you, and it needs nothing instrumented — it reads the same tables
`manage.py harel_worker` does.

## A machine's own page

Click a machine's name for:

- **its diagram** (below);
- **its shape** — which states are orthogonal (fork into regions) and what it invokes, each
  target linked to its own page when it's a machine `conf.definitions()` knows;
- the same operational row the Machines list shows, for this one machine;
- its **top-level executions**.

### Top-level executions, not every row

A machine's executions aren't all equally worth listing on their own. Take an order whose
fulfilment forks into shipping and billing (orthogonal regions) and ships one parcel per item
(a fan-out `invoke`): one order is six executions — the root, two regions, three parcels. Only
two of those six are worth a row of their own here:

- the **root** (no parent) — `fulfilment`'s row for that order;
- each **invoked submachine's own entry point** (a parent that runs a *different* machine) —
  `parcels.parcel`'s own rows, one per parcel, each also reachable from the order's tree.

An **orthogonal region** — a child that runs the *same* machine as its parent — never gets a row
here, on the Machines list, or anywhere else on its own: a region only means something next to
its siblings, so it's shown nested, inside its own unit's tree (below).

Each row also shows **how many executions inside its own tree are `FAILED`** — not just its own
status. A root can read `RUNNING` while a region three levels down is dead-lettered; this is
where you'd otherwise miss it.

## An execution's own page

Everything about one execution, in sections:

- **Status, outcome, state** and the error that dead-lettered it, with its timestamps.
- **Machine** — the machine's diagram, and the states the execution is in: inside an
  orthogonal state, the one each region is in.
- **Parent and children** — its whole tree: itself, every orthogonal region and invoked child,
  and theirs, indented by depth. A region is only ever shown here — never as its own row
  anywhere else.
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

## The list and search

**Executions** itself (the entry you land on from the sidebar) is a flat, searchable list of
every execution — every machine, every region, every invoke, no structure. Use it to search by
id or to filter across machines (by status, by machine, or **"top-level only"** — the same rule
the Machines page uses: no region, ever, standing alone). It's the tool for "find this one
record", not for understanding how a machine's executions relate to each other — for that, start
from Machines instead.

## Actions

Run from an execution's own row or page:

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
