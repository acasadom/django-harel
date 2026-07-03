"""Sphinx configuration for the django-harel documentation (Markdown via MyST)."""

project = "django-harel"
author = "Alberto Casado"

extensions = ["myst_parser", "sphinxcontrib.mermaid"]

source_suffix = {".md": "markdown"}
root_doc = "index"

exclude_patterns = ["_build"]

html_theme = "furo"

myst_heading_anchors = 3

suppress_warnings = ["misc.highlighting_failure"]
