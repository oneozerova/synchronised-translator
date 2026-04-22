"""
Compatibility launcher for the new frontend.
Primary frontend is at frontend/app.py.
"""

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT / "frontend" / "app.py"), run_name="__main__")
