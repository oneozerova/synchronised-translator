"""
Legacy entrypoint.
Use: streamlit run frontend/app.py
"""

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parent
runpy.run_path(str(ROOT / "frontend" / "app.py"), run_name="__main__")
