"""Development entrypoint: python run.py"""
from __future__ import annotations

import os
import pathlib


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so no extra dependency is needed."""
    env_file = pathlib.Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    load_dotenv()
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=os.environ.get("LEADGEN_HOST", "127.0.0.1"),
        port=int(os.environ.get("LEADGEN_PORT", "8000")),
        reload=os.environ.get("LEADGEN_RELOAD", "0") == "1",
    )
