"""Turn frames from scripts/record_demo.py into README GIFs (and an MP4) with ffmpeg.

    uv run --with pillow python scripts/make_gifs.py

Typing plays at real speed, long waits for the models are cut short, and the debate plays as a timelapse.
"""

import json
import os
import subprocess
import tempfile

from PIL import Image

ROOT = os.path.join(os.path.dirname(__file__), "..")
FRAMES = os.path.join(ROOT, ".demo-frames")
OUT = os.path.join(ROOT, "docs", "images")

# How each phase is paced: real time between frames is multiplied by SPEED and clamped to
# [MIN, MAX] seconds. Typing stays natural, waiting for models is sped up, and the debate is a
# timelapse at a fixed frame length.
SPEED = {"ask": 1.0, "interview": 0.5, "debate": None, "answer": 1.0, "simple": 0.35, "expert": 0.35}
MIN = {"ask": 0.04, "interview": 0.04, "debate": 0.2, "answer": 0.05, "simple": 0.05, "expert": 0.05}
MAX = {"ask": 0.8, "interview": 0.7, "debate": 0.2, "answer": 0.8, "simple": 0.5, "expert": 0.6}
DEBATE_FRAMES = 110  # the debate is sampled down to this many frames
HOLD_LAST = 2.5


def fingerprint(path):
    img = Image.open(path).convert("L").resize((64, 40))
    return img.tobytes()


def build_timeline(frames, phases, debate_frames=DEBATE_FRAMES):
    """Pick frames and durations for the given phases."""
    picked = [f for f in frames if f["phase"] in phases]
    debate = [f for f in picked if f["phase"] == "debate"]
    if len(debate) > debate_frames:
        step = len(debate) / debate_frames
        keep = {id(debate[int(i * step)]) for i in range(debate_frames)}
        picked = [f for f in picked if f["phase"] != "debate" or id(f) in keep]
    timeline, last = [], None
    for i, f in enumerate(picked):
        phase = f["phase"]
        gap = (picked[i + 1]["t"] - f["t"]) if i + 1 < len(picked) else MAX[phase]
        duration = MIN[phase] if SPEED[phase] is None else min(max(gap * SPEED[phase], MIN[phase]), MAX[phase])
        path = os.path.join(FRAMES, f["file"])
        fp = fingerprint(path)
        if fp == last:  # nothing changed on screen: stretch the previous frame a little instead
            timeline[-1][1] = min(timeline[-1][1] + duration * 0.3, 1.2)
            continue
        timeline.append([path, duration])
        last = fp
    if timeline:
        timeline[-1][1] += HOLD_LAST
    return timeline


def concat_file(timeline):
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as fh:
        for frame, duration in timeline:
            fh.write(f"file '{frame}'\nduration {duration:.3f}\n")
        fh.write(f"file '{timeline[-1][0]}'\n")  # concat demuxer needs the last frame twice
    return path


def gif(timeline, name, width=960, fps=12):
    listing = concat_file(timeline)
    out = os.path.join(OUT, name)
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                    "-vf", vf, "-loop", "0", out], check=True)
    os.remove(listing)
    seconds = sum(d for _, d in timeline)
    print(f"{name}: {len(timeline)} frames, {seconds:.0f}s, {os.path.getsize(out) / 1e6:.1f} MB")


def mp4(timeline, name):
    listing = concat_file(timeline)
    out = os.path.join(OUT, name)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                    "-vf", "fps=30,scale=1280:-2:flags=lanczos", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "23", "-movflags", "+faststart", out], check=True)
    os.remove(listing)
    print(f"{name}: {os.path.getsize(out) / 1e6:.1f} MB")


def main():
    with open(os.path.join(FRAMES, "frames.json")) as fh:
        frames = json.load(fh)
    os.makedirs(OUT, exist_ok=True)
    everything = ("ask", "interview", "debate", "answer", "simple", "expert")
    gif(build_timeline(frames, everything, debate_frames=45), "demo.gif", width=880, fps=10)
    gif(build_timeline(frames, ("ask", "interview")), "demo-interview.gif")
    gif(build_timeline(frames, ("debate",)), "demo-debate.gif")
    gif(build_timeline(frames, ("answer", "simple", "expert")), "demo-answer.gif")
    mp4(build_timeline(frames, everything), "demo.mp4")


if __name__ == "__main__":
    main()
