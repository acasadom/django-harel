# 2. The state machine

We model an **article publishing workflow** with five possible outcomes:

```
Draft ──(Submit)──▶ InReview ──(Approve)──▶ Approved ──▶ Published ✓
                        │
                        └──(Reject)──▶ Rejected ✗
Draft/InReview ──(Archive)──▶ Archived ✗
```

## The `.stm` file

Create `articles/machines/publishing.stm`:

```
event Submit  {}
event Approve {}
event Reject  { note: string }
event Archive {}

bind {
  on_in_review = articles.actions.on_in_review
  on_approved  = articles.actions.on_approved
  on_published = articles.actions.on_published
  on_rejected  = articles.actions.on_rejected
  on_archived  = articles.actions.on_archived
}

machine publishing {
  context {
    article_id: int
  }

  initial Draft

  state Draft    {}
  state InReview { on enter on_in_review }
  state Approved { on enter on_approved }

  final Published  success    { on enter on_published }
  final Rejected   failure    { on enter on_rejected }
  final Archived   cancelled  { on enter on_archived }

  from Draft    to InReview on Submit
  from InReview to Approved on Approve
  from Approved to Published
  from InReview to Rejected on Reject
  from Draft    to Archived on Archive
  from InReview to Archived on Archive
}
```

## What each section does

**`event` declarations** name every event the machine can receive and optionally declare
typed fields. `Reject { note: string }` means the event carries a `note` string — accessible
in actions via `event.data["note"]`.

**`bind` block** maps short action names used inside the machine to fully-qualified Python
dotted paths. harel resolves them with `importlib.import_module` at runtime — no import at
definition time, no circular-import issues.

**`context { ... }`** declares the data each execution carries. `create()` checks it, so an
execution without its `article_id` is refused before anything is written.

**`state` / `final`** declare the states. `final` states end the machine with a verdict
— its *outcome* (`success`, `failure`, `cancelled`). States with `on enter` call the bound action every time
the machine enters them.

**`from … to … on …`** declares transitions. A transition with no `on` fires automatically
when the source state is entered — that is why `Approved → Published` happens immediately
after the editor approves: no extra event is needed, and both `on_approved` and `on_published`
run in the same step.

Check it before running it — unreachable states, transitions that can never fire, terminals
without an outcome:

```bash
harel validate articles/machines/publishing.stm
```

## Register in settings

```python
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
}
```

Next: [write the action functions](03-actions).
