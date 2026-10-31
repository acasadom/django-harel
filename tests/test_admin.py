"""The executions in the Django admin."""

import pytest
from django.contrib.admin.utils import quote
from django.contrib.auth.models import Permission, User
from django.urls import reverse
from harel.engine.execution import Status
from harel.spec.states import Event

from django_harel import conf

pytestmark = pytest.mark.django_db(transaction=True)

CHANGELIST = "admin:django_harel_harelexecution_changelist"


@pytest.fixture
def jobs():
    runner = conf.durable_runner()
    running = runner.create("job", execution_id="job-running")
    done = runner.create("job", execution_id="job-done")
    runner.process(done.id, Event(kind="Finish"))
    return running, done


def test_the_list_shows_and_filters_by_status(admin_client, jobs):
    page = admin_client.get(reverse(CHANGELIST))
    assert page.status_code == 200
    assert b"job-running" in page.content and b"job-done" in page.content

    done_only = admin_client.get(reverse(CHANGELIST), {"status": "DONE"})
    assert b"job-done" in done_only.content and b"job-running" not in done_only.content

    roots = admin_client.get(reverse(CHANGELIST), {"top_level": "1"})
    assert b"job-running" in roots.content


def test_the_detail_page_renders(admin_client, jobs):
    page = admin_client.get(reverse("admin:django_harel_harelexecution_change", args=["job-done"]))
    assert page.status_code == 200
    assert b"Done" in page.content


def test_the_actions_run_the_control_plane(admin_client, jobs):
    url = reverse(CHANGELIST)
    admin_client.post(url, {"action": "suspend", "_selected_action": ["job-running"]})
    assert conf.durable_runner().store.load("job-running").status is Status.SUSPENDED

    admin_client.post(url, {"action": "resume", "_selected_action": ["job-running"]})
    assert conf.durable_runner().store.load("job-running").status is Status.RUNNING

    admin_client.post(url, {"action": "purge", "_selected_action": ["job-done", "job-running"]})
    store = conf.durable_runner().store
    assert store.load("job-done") is None
    assert store.load("job-running") is not None  # not finished: refused


def test_a_viewer_can_look_but_not_operate(client, jobs):
    viewer = User.objects.create_user("viewer", password="x", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_harelexecution"))
    client.force_login(viewer)

    page = client.get(reverse(CHANGELIST))
    assert page.status_code == 200
    assert b'value="suspend"' not in page.content


def test_redrive_asks_for_the_state_then_brings_failed_executions_back(admin_client):
    from tests.app.models import Order, Stock

    Stock.objects.create(item="widget", available=1)
    runner = conf.durable_runner()
    failed = [
        runner.create("order", context={"order_id": Order.objects.create(item="widget", qty=5).pk}).id
        for _ in range(2)
    ]
    assert all(runner.store.load(i).status is Status.FAILED for i in failed)
    url = reverse(CHANGELIST)

    page = admin_client.post(url, {"action": "redrive", "_selected_action": failed})
    assert page.status_code == 200
    body = page.content.decode()
    assert 'value="Reserving" selected' in body, "suggests where they failed"
    assert 'value="AwaitingPayment"' in body
    assert 'value="Paid"' not in body and 'value="Expired"' not in body, "never a terminal"

    admin_client.post(
        url, {"action": "redrive", "_selected_action": failed, "apply": "1", "target": "AwaitingPayment"}
    )
    for execution_id in failed:
        exe = runner.store.load(execution_id)
        assert exe.status is Status.RUNNING and exe.active_path == "AwaitingPayment"
    assert runner.process(failed[0], Event(kind="Pay")).outcome == "success"


def test_redrive_refuses_executions_that_did_not_fail(admin_client, jobs):
    page = admin_client.post(
        reverse(CHANGELIST), {"action": "redrive", "_selected_action": ["job-running"]}, follow=True
    )
    assert b"Redrive takes failed executions only" in page.content
    assert conf.durable_runner().store.load("job-running").status is Status.RUNNING


# --- the change page -----------------------------------------------------------------------------


def _change_page(client, execution_id):
    page = client.get(reverse("admin:django_harel_harelexecution_change", args=[execution_id]))
    assert page.status_code == 200
    return page.content.decode()


@pytest.fixture
def widgets():
    from tests.app.models import Stock

    return Stock.objects.create(item="widget", available=50)


def test_the_page_draws_the_machine_and_where_it_is(admin_client, widgets):
    from tests.app.models import Order

    exe = conf.durable_runner().create(
        "order", context={"order_id": Order.objects.create(item="widget", qty=1).pk}
    )
    body = _change_page(admin_client, exe.id)

    assert '<pre class="harel-diagram">stateDiagram-v2' in body
    assert "In: <code>AwaitingPayment</code>" in body
    assert "class AwaitingPayment active" in body, "highlighted in the diagram"
    assert "mermaid@11.15.0/dist/mermaid.min.js" in body and "django_harel/admin.js" in body


def test_regions_are_listed_with_their_states_and_linked(admin_client, widgets):
    from tests.app.models import Order

    order = Order.objects.create(item="widget", qty=2)
    exe = conf.durable_runner().create("fulfilment", context={"order_id": order.pk, "items": ["a"]})
    body = _change_page(admin_client, exe.id)

    assert "<code>Fulfilling.Billing.Charging</code>" in body, "the region waiting for Paid"
    highlighted = body.split("class ")[-1].split(" active")[0].split(",")
    assert "Fulfilling_Billing_Charging" in highlighted, "each region's state highlighted"
    assert "Fulfilling" not in highlighted, "not the orthogonal state containing them"
    regions = [cid for cid, child in exe.children.items() if not child.submachine]
    assert len(regions) == 2
    for region_id in regions:
        assert reverse("admin:django_harel_harelexecution_change", args=[quote(region_id)]) in body
    assert "<td>region</td>" in body

    region_page = _change_page(admin_client, regions[0])
    assert "Parent: " in region_page and exe.id in region_page


def test_the_queue_and_the_timers_are_shown(admin_client, widgets):
    from django.db import connection

    from tests.app.models import Order

    runner = conf.distributed_runner()
    exe = runner.create("order", context={"order_id": Order.objects.create(item="widget", qty=1).pk})
    body = _change_page(admin_client, exe.id)
    assert "<code>Start</code>" in body, "its Start, queued"

    runner.worker(worker_id="w").step()  # runs the Start: AwaitingPayment arms its 60 s timeout
    body = _change_page(admin_client, exe.id)
    assert "<code>Start</code>" not in body
    assert "<code>AwaitingPayment</code>" in body and "overdue" not in body

    with connection.cursor() as cur:  # due an hour ago, and no worker fired it
        cur.execute("UPDATE harel_timers SET fire_at = fire_at - 3660 WHERE execution_id = %s", [exe.id])
    assert "overdue — is a worker running?" in _change_page(admin_client, exe.id)


def test_the_timeline_lists_the_recorded_steps(admin_client, widgets, settings):
    from tests.app.models import Order

    settings.DJANGO_HAREL = {**settings.DJANGO_HAREL, "TRACE": True}
    runner = conf.durable_runner()
    exe = runner.create("order", context={"order_id": Order.objects.create(item="widget", qty=1).pk})
    runner.process(exe.id, Event(kind="Pay"))
    body = _change_page(admin_client, exe.id)

    assert "AwaitingPayment → Paid" in body
    assert "<code>tests.app.actions.mark_paid</code>" in body
    assert body.index("AwaitingPayment → Paid") < body.index("(start) → AwaitingPayment"), "latest first"


def test_a_state_the_machine_no_longer_has_is_drawn_without_the_highlight():
    from django_harel.admin import diagram_source

    source = diagram_source(conf.definitions()["order"], ["Removed"])
    assert source.startswith("stateDiagram-v2") and "classDef active" not in source


# --- the machines catalog --------------------------------------------------------------------------


MACHINES = "admin:django_harel_harelmachine_changelist"


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_machines_lists_every_configured_machine(admin_client, jobs):
    page = admin_client.get(reverse(MACHINES))
    assert page.status_code == 200
    body = page.content.decode()
    for definition_id in ("job", "order", "fulfilment"):
        assert f">{definition_id}</a>" in body, f"{definition_id} is linked to its own page"
    assert "PENDING: 1" not in body  # jobs fixture creates no PENDING job


def test_machines_shows_status_counts_linked_to_the_filtered_list(admin_client, jobs):
    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "RUNNING: 1" in body
    assert "DONE: 1" in body
    assert "definition_id=job&amp;status=RUNNING" in body or "definition_id=job&status=RUNNING" in body


def test_a_failed_execution_is_linked_and_overflow_is_counted(admin_client):
    from tests.app.models import Order, Stock

    Stock.objects.create(item="widget", available=1)
    runner = conf.durable_runner()
    failed_ids = [
        runner.create("order", context={"order_id": Order.objects.create(item="widget", qty=5).pk}).id
        for _ in range(12)
    ]

    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "FAILED: 12" in body
    shown, hidden = sorted(failed_ids)[:10], sorted(failed_ids)[10:]
    for execution_id in shown:
        assert execution_id in body
    for execution_id in hidden:
        assert execution_id not in body
    assert "… and 2 more" in body


def test_the_queue_size_and_oldest_age_are_shown(admin_client, monkeypatch):
    """The machines page reads "now" from `time.time()` (it isn't tied to any one runner's clock),
    so the fake clock here stands in for it too — `inspection.time.time` is patched to it."""
    from harel.engine.distributed import DistributedRunner

    import django_harel.inspection as inspection
    from django_harel.store import DjangoStore
    from django_harel.transport import DjangoTransport

    clock = Clock()
    monkeypatch.setattr(inspection.time, "time", clock)
    runner_store, transport = DjangoStore(), DjangoTransport(clock=clock)
    runner = DistributedRunner(runner_store, transport, conf.definitions(), clock=clock, execution="inline")
    runner.create("job", execution_id="waiting", start_on_create=True)

    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "1 queued" in body

    clock.now += 125  # 2m 5s later: nothing drained it (no worker ran)
    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "1 queued" in body and "oldest waiting" in body and "2m 5s" in body


def test_overdue_timers_are_flagged(admin_client):
    from django.db import connection

    from tests.app.models import Order, Stock

    Stock.objects.create(item="widget", available=5)
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("order", context={"order_id": order.pk})
    runner.worker(worker_id="w").step()  # runs Start: AwaitingPayment arms its 60s timeout

    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "none overdue" in body

    with connection.cursor() as cur:
        cur.execute("UPDATE harel_timers SET fire_at = fire_at - 3660 WHERE execution_id = %s", [exe.id])
    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "1 overdue — is a worker running?" in body


def _now(monkeypatch, clock):
    import django_harel.admin as harel_admin
    import django_harel.inspection as inspection

    monkeypatch.setattr(inspection.time, "time", clock)
    monkeypatch.setattr(harel_admin.time, "time", clock)


def test_a_queue_no_live_worker_claims_is_flagged(admin_client, monkeypatch):
    from harel.engine.store.base import WorkerRecord

    from django_harel.store import DjangoStore
    from django_harel.transport import DjangoTransport

    clock = Clock()
    _now(monkeypatch, clock)
    DjangoTransport(clock=clock).publish("o1", Event(kind="Go"), routing_key="order")
    DjangoStore().record_worker(WorkerRecord("w-job", ("job",), 900.0, 1_000.0, 10.0))

    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "none claims it — is a worker running?" in body
    assert "<td>job</td>" in body  # a dedicated worker's machine shows with nothing queued

    DjangoStore().record_worker(WorkerRecord("w-all", None, 900.0, 1_000.0, 10.0))
    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "none claims it" not in body and "w-all" in body and "every machine" in body


def test_a_gone_worker_can_be_forgotten_and_a_live_one_cannot(admin_client, client, monkeypatch):
    from harel.engine.store.base import WorkerRecord

    from django_harel.store import DjangoStore

    clock = Clock()
    _now(monkeypatch, clock)
    store = DjangoStore()
    store.record_worker(WorkerRecord("live", ("order",), 900.0, 995.0, 10.0))
    store.record_worker(WorkerRecord("gone", None, 100.0, 200.0, 10.0))

    body = admin_client.get(reverse(MACHINES)).content.decode()
    assert "alive" in body and "gone" in body and "13m 20s ago" in body

    forget = "admin:django_harel_harelmachine_forget_worker"
    viewer = User.objects.create_user("viewer3", password="x", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_harelexecution"))
    client.force_login(viewer)
    assert "Forget" not in client.get(reverse(MACHINES)).content.decode()
    assert client.post(reverse(forget, args=["gone"])).status_code == 403

    assert admin_client.get(reverse(forget, args=["gone"])).status_code == 403
    admin_client.post(reverse(forget, args=["live"]))
    admin_client.post(reverse(forget, args=["gone"]))
    assert [w.worker_id for w in store.list_workers()] == ["live"]


def test_a_viewer_can_see_machines_without_operate_permission(client, jobs):
    viewer = User.objects.create_user("viewer2", password="x", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_harelexecution"))
    client.force_login(viewer)
    assert client.get(reverse(MACHINES)).status_code == 200


def test_someone_without_view_permission_is_refused(client, jobs):
    plain = User.objects.create_user("plain", password="x", is_staff=True)
    client.force_login(plain)
    assert client.get(reverse(MACHINES)).status_code == 403


def test_machines_and_executions_are_both_in_the_sidebar(admin_client):
    """Not a link buried on one page: "Machines" (the `.stm` definitions) sits next to
    "Executions" (instances of them) everywhere in the admin, as its own navigable entry —
    reachable without knowing its URL."""
    body = admin_client.get(reverse("admin:index")).content.decode()
    assert reverse(MACHINES) in body and "Machines" in body
    assert reverse(CHANGELIST) in body and "Executions" in body


# --- a machine's own page --------------------------------------------------------------------------


def _machine_page(client, definition_id):
    page = client.get(reverse("admin:django_harel_harelmachine_detail", args=[definition_id]))
    assert page.status_code == 200
    return page.content.decode()


def test_a_machines_page_shows_its_diagram_and_shape(admin_client):
    body = _machine_page(admin_client, "fulfilment")
    assert '<pre class="harel-diagram">stateDiagram-v2' in body
    assert "mermaid@11.15.0/dist/mermaid.min.js" in body and "django_harel/admin.js" in body, (
        "a custom TemplateResponse doesn't get the change form's Media for free"
    )
    assert "<code>Fulfilling</code> forks into: <code>Shipping</code>, <code>Billing</code>" in body
    assert "<code>Fulfilling.Shipping.Packing</code> invokes" in body
    assert reverse("admin:django_harel_harelmachine_detail", args=["parcels.parcel"]) in body


def test_a_leaf_machine_has_no_shape_section(admin_client):
    body = _machine_page(admin_client, "parcels.parcel")
    assert "<h2>Shape</h2>" not in body


def test_an_unknown_machine_is_a_404(admin_client):
    page = admin_client.get(reverse("admin:django_harel_harelmachine_detail", args=["no-such-machine"]))
    assert page.status_code == 404


def test_someone_without_view_permission_is_refused_a_machine_page(client):
    plain = User.objects.create_user("plain2", password="x", is_staff=True)
    client.force_login(plain)
    page = client.get(reverse("admin:django_harel_harelmachine_detail", args=["job"]))
    assert page.status_code == 403


def test_top_level_units_never_include_a_bare_region(admin_client, widgets):
    from tests.app.models import Order

    order = Order.objects.create(item="widget", qty=1)
    exe = conf.durable_runner().create("fulfilment", context={"order_id": order.pk, "items": ["a"]})
    regions = [cid for cid, child in exe.children.items() if not child.submachine]
    assert len(regions) == 2

    fulfilment_page = _machine_page(admin_client, "fulfilment")
    assert f"<code>{exe.id}</code>" in fulfilment_page
    for region_id in regions:
        assert region_id not in fulfilment_page, "a region never gets its own row"

    parcel_page = _machine_page(admin_client, "parcels.parcel")
    # the parcel is invoked by the Shipping region, not by the root, but is still its own unit
    shipping = next(r for r in regions if conf.durable_runner().store.load(r).root_path.endswith("Shipping"))
    parcel_ids = [cid for cid, c in conf.durable_runner().store.load(shipping).children.items()]
    assert parcel_ids, "the fixture has at least one item"
    for parcel_id in parcel_ids:
        assert f"<code>{parcel_id}</code>" in parcel_page


def test_a_failure_nested_two_levels_down_rolls_up_to_the_root(admin_client, widgets):
    from tests.app.models import Order

    runner = conf.durable_runner()
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("fulfilment", context={"order_id": order.pk, "items": ["a"], "card_ok": False})
    runner.process(exe.id, Event(kind="Paid"))  # Billing.Charging raises, unhandled: dead-letters the region

    reloaded = runner.store.load(exe.id)
    assert reloaded.status is Status.RUNNING, "the root itself is untouched by its region's failure"

    body = _machine_page(admin_client, "fulfilment")
    row_start = body.index(f"<code>{exe.id}</code>")
    row = body[row_start : row_start + 400]
    assert "RUNNING" in row
    assert '<span class="harel-alert">1</span>' in row, "one failed execution inside this unit's tree"


def test_the_failed_region_itself_is_findable_from_the_tree(admin_client, widgets):
    from tests.app.models import Order

    runner = conf.durable_runner()
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("fulfilment", context={"order_id": order.pk, "items": ["a"], "card_ok": False})
    runner.process(exe.id, Event(kind="Paid"))

    # the root's own page points at its tree, which is where the failed region actually lives
    root_page = _change_page(admin_client, exe.id)
    billing = next(
        cid
        for cid, c in runner.store.load(exe.id).children.items()
        if not c.submachine and runner.store.load(cid).root_path.endswith("Billing")
    )
    assert f"<code>{billing}</code>" in root_page

    region_page = _change_page(admin_client, billing)
    assert ">FAILED<" in region_page
    assert "CardRefused" in region_page
