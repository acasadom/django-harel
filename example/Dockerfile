# The example app, for compose.yaml: Python and uv only. The code isn't copied in — compose
# mounts this repository and the harel checkout next to it, so a change to either is live.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

# git: both projects take their version from their git history (hatch-vcs). The checkouts are
# mounted from the host, owned by another user: tell git that's fine.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

# the container's own environment — never the host's .venv inside the mounted checkout
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app/django-harel/example
