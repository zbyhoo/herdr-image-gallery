#!/usr/bin/env python3
"""Forward to the plugin checkout, including when the skill is symlinked."""
from pathlib import Path
import runpy
import sys

entrypoint = Path(__file__).resolve().parents[3] / "gallery.py"
sys.argv[0] = str(entrypoint)
runpy.run_path(str(entrypoint), run_name="__main__")
