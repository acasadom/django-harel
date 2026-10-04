"""The executions in the Django admin."""

import pytest
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
