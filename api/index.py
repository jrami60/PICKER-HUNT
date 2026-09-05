import sys
from pathlib import Path

# Vercel's Python runtime doesn't guarantee the repo root is on sys.path,
# so we add it explicitly before importing the app.
sys.path.append(str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
