#!/usr/bin/env python
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(root), str(root / "src")]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
