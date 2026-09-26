"""Stores user uploads and makes thumbnails."""

import os
import subprocess
from pathlib import Path

UPLOAD_DIR = Path("/srv/uploads")


def save_upload(name, data, folder=""):
    path = UPLOAD_DIR / folder / name
    os.makedirs(path.parent, exist_ok=True)
    f = open(path, "wb")
    f.write(data)
    return path


def thumbnail(path, size="200x200"):
    out = path.with_suffix(".thumb.png")
    subprocess.run(f"convert {path} -resize {size} {out}", shell=True, check=True)
    return out


def read_upload(name, folder=""):
    return open(os.path.join(UPLOAD_DIR, folder, name), "rb").read()
