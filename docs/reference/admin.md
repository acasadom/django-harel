# Admin

With `django.contrib.admin` installed, **Harel › Executions** lists every execution.

## The list

Each row shows the execution's id, machine, status, outcome, current state, when it last
changed and its version. Filter by status, by machine, or to root executions only (no
orthogonal regions or invoked children); search by id.

## The detail page

Everything about one execution: status, outcome, state, the error that dead-lettered it,
its timestamps, parent and children, its context, and its recent steps when tracing is on
(`DJANGO_HAREL["TRACE"] = True`).

Nothing here can be edited: only the machine changes an execution.

## Actions

| Action | |
|---|---|
| Suspend / Resume | pause an execution and continue it |
| Cancel | run the machine's `on Cancel` cleanup (on a worker), or terminate |
| Terminate | end it now, without cleanup |
| Purge | delete finished executions and their children; one still running is refused |

They are offered to users with the `django_harel.change_harelexecution` permission; the
`view_harelexecution` permission shows the pages without them.
