# 6. A machine in the request

Some machines are driven by the user, one click at a time: a signup wizard, a checkout, a
multi-step form. The request moves the machine on and renders where it is now. A queue would
only get in the way — the user is waiting for the answer.

`django_harel.durable_runner()` runs the event **in the request's thread, to the end**: the
transition, its actions, the commit. Nothing is queued and no worker is involved.

## A checkout

```
event Address { street: string  city: string }
event Pay     { token: string }
event Back    {}

machine checkout {
  context {
    cart_id: int
  }

  initial Shipping

  state Shipping {}
  state Payment  {}

  final Placed success { on enter shop.actions.place_order }

  from Shipping to Payment  on Address
  from Payment  to Shipping on Back
  from Payment  to Placed   on Pay
}
```

```python
# shop/views.py
from django.db import transaction
from django.shortcuts import redirect, render
from harel.spec.states import Event

from django_harel import durable_runner


def start_checkout(request, cart_id):
    exe = durable_runner().create("checkout", context={"cart_id": cart_id})
    request.session["checkout"] = exe.id
    return redirect("checkout")


def checkout(request):
    runner = durable_runner()
    exe_id = request.session["checkout"]
    if request.method == "POST":
        with transaction.atomic():
            exe = runner.process(exe_id, Event(kind=request.POST["event"], data=request.POST.dict()))
    else:
        exe = runner.store.load(exe_id)
    # one template per state: checkout/Shipping.html, checkout/Payment.html, checkout/Placed.html
    return render(request, f"checkout/{exe.active_path}.html", {"exe": exe})
```

The machine, not the view, decides which step comes next and which events each step
accepts. An event the current state doesn't handle — a `Pay` posted twice, a stale form —
doesn't move it. One that lacks a field its declaration requires (`Pay` without its `token`)
raises `harel.EventError` before anything runs.

## One transaction

`process()` runs inside the view's `transaction.atomic()`: the machine's new state and
everything its actions wrote commit together. If the view fails after `process()` returns,
the machine's step rolls back with it.

By default an action that raises dead-letters the execution (`FAILED`), like on a worker.
In the request you may prefer the exception itself:

```python
DJANGO_HAREL = {
    ...
    "ON_ACTION_ERROR": "raise",
}
```

Then the exception reaches the view, and the step isn't committed — the user can fix the input
and try again from the same state.

## Mixing both

An execution in the request and the workers share the same tables. A wizard can run in the
request and, when the user confirms, hand the slow part to the workers: `send()` an event
through `distributed_runner()`, and let the worker take it from there.

If the request and a worker ever advance the same execution at the same moment, the second
to commit gets `StoreConflict` (harel's optimistic concurrency): nothing is overwritten, and
the request can reload and retry.

## Timers need a worker

A `timeout` in a machine you run in the request — an abandoned cart that expires after an
hour — is armed in the database like any other, but fired by `manage.py harel_worker`. Run
one, even if all your other machines run in the request.

## Async views

The runners work in the request's own thread and refuse to run inside an event loop. From an
`async def` view, call them through `asgiref.sync.sync_to_async`. Synchronous views are fine
under ASGI too.
