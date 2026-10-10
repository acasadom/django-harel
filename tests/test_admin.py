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

    roots = admin_client.get(reverse(CHANGELIST), {"roots": "1"})
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
    assert "mermaid@11.15.0/dist/mermaid.min.js" in body and "django_harel/admin.js" in body


def test_regions_are_listed_with_their_states_and_linked(admin_client, widgets):
    from tests.app.models import Order

    order = Order.objects.create(item="widget", qty=2)
    exe = conf.durable_runner().create("fulfilment", context={"order_id": order.pk, "items": ["a"]})
    body = _change_page(admin_client, exe.id)

    assert "<code>Fulfilling.Billing.Charging</code>" in body, "the region waiting for Paid"
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
