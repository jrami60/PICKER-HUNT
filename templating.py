"""Shared Jinja2Templates instance.

Anchored to this file's own directory (not the process cwd) so template
resolution works the same locally, in Docker, or on a serverless host like
Vercel where the working directory isn't guaranteed to be the repo root.

Import `templates` from here everywhere instead of each module creating its
own `Jinja2Templates(directory="templates")` (that was a 7x DRY violation).
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
