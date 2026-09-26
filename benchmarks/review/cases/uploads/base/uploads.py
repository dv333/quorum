"""Stores user uploads and makes thumbnails."""

import subprocess
from pathlib import Path

UPLOAD_DIR = Path("/srv/uploads")


def save_upload(name, data):
    safe = Path(name).name
    if not safe or safe.startswith("."):
        raise ValueError("bad file name")
    path = UPLOAD_DIR / safe
    path.write_bytes(data)
    return path


def thumbnail(path):
    out = path.with_suffix(".thumb.png")
    subprocess.run(["convert", str(path), "-resize", "200x200", str(out)], check=True, timeout=30)
    return out


def read_upload(name):
    return (UPLOAD_DIR / Path(name).name).read_bytes()
