#!/bin/sh
# Runs in both containers, with the command given — "web" or "worker". The web container syncs
# the shared environment (fast after the first time: the uv cache is a volume) and migrates;
# the worker waits for both, so they never write the environment at once.
set -e
cd /app/django-harel/example
PY=/opt/venv/bin/python

case "$1" in
  web)
    (cd .. && uv sync --quiet)
    $PY manage.py migrate --noinput
    $PY manage.py createsuperuser --noinput 2>/dev/null || true   # from DJANGO_SUPERUSER_*
    $PY manage.py seed_demo
    exec $PY manage.py runserver 0.0.0.0:8000
    ;;
  worker)
    # wait for the web container's sync and migrations
    until [ -x $PY ] && $PY manage.py migrate --check >/dev/null 2>&1; do sleep 1; done
    exec $PY manage.py harel_worker
    ;;
  *)
    exec "$@"
    ;;
esac
