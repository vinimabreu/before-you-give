"""Vercel entry point (zero-config FastAPI): the same app, with its scratch
space in /tmp and the narrations in Vercel Blob (BLOB_READ_WRITE_TOKEN)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BYG_TRUST_PROXY", "1")

from before_you_give.app import create_app  # noqa: E402

app = create_app(cache_dir=Path("/tmp/before-you-give"))
