#!/usr/bin/env python3
"""
Build a batch of HEM hologram videos for the HEM TikTok Poster add-on.

Input: a JSON file with a list of posts:
  [{"quote": "...", "highlight": "word", "caption": "...", "hashtags": ["#HybridEliteMafia", ...]}, ...]

For each post it:
  1. burns the quote into 322683.mp4 with main.render_video (same look as the pipeline),
  2. re-encodes it smaller (H.264, CRF 23) into queue/videos/<id>.mp4,
  3. gives it the next free post slot (default 8:05 AM, 1:05 PM, 8:05 PM Chicago time),
  4. appends it to queue/queue.json and logs the quote in history.json.

No API key is needed. Usage:
  python tools/make_batch.py posts.json
  python tools/make_batch.py posts.json --start 2026-11-19 --slots 08:05,13:05,20:05
"""

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402  (reuses the pipeline's renderer and history helpers)

QUEUE_DIR = ROOT / "queue"
VIDEO_DIR = QUEUE_DIR / "videos"
QUEUE_FILE = QUEUE_DIR / "queue.json"
TZ = ZoneInfo("America/Chicago")
MIN_LEAD = dt.timedelta(hours=1)


def load_queue():
    if QUEUE_FILE.exists():
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"items": data}
    return {"items": []}


def slug(text, n=5):
    words = re.findall(r"[a-z0-9]+", text.lower())[:n]
    return "-".join(words) or "post"


def slot_times(start_day, slots, taken, count):
    """Yield `count` future slot datetimes (Chicago) not already in `taken`."""
    now = dt.datetime.now(TZ)
    day = start_day
    out = []
    while len(out) < count:
        for hhmm in slots:
            h, m = (int(x) for x in hhmm.split(":"))
            if m % 5:
                raise SystemExit(f"Slot {hhmm}: minutes must be a multiple of 5 (TikTok's picker uses 5-minute steps)")
            t = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
            if t - now < MIN_LEAD or t.isoformat() in taken:
                continue
            out.append(t)
            if len(out) == count:
                break
        day += dt.timedelta(days=1)
    return out


def main_cli():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("posts", type=Path, help="JSON file with the posts to build")
    ap.add_argument("--start", help="first day to use, YYYY-MM-DD (default: today)")
    ap.add_argument("--slots", default="08:05,13:05,20:05", help="daily post times, Chicago time")
    ap.add_argument("--style", default=None, help="text style: neon, street, boss, stencil, plain")
    args = ap.parse_args()

    posts = json.loads(args.posts.read_text(encoding="utf-8"))
    if not isinstance(posts, list) or not posts:
        raise SystemExit("posts file must be a non-empty JSON list")
    for i, p in enumerate(posts, 1):
        for key in ("quote", "caption"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise SystemExit(f"post {i}: missing '{key}'")

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not installed")

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    queue = load_queue()
    taken = {it["postAt"] for it in queue["items"]}
    start = dt.date.fromisoformat(args.start) if args.start else dt.datetime.now(TZ).date()
    times = slot_times(start, [s.strip() for s in args.slots.split(",") if s.strip()], taken, len(posts))
    history = main.load_history()

    for post, when in zip(posts, times):
        item_id = f"{when:%Y%m%d-%H%M}-{slug(post['quote'])}"
        main.render_video(post["quote"], post.get("highlight"), args.style)
        dest = VIDEO_DIR / f"{item_id}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(main.OUTPUT),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)],
            check=True,
        )
        tags = [t if t.startswith("#") else f"#{t}" for t in post.get("hashtags", [])]
        queue["items"].append({
            "id": item_id,
            "video": f"videos/{dest.name}",
            "caption": post["caption"].strip(),
            "hashtags": tags,
            "postAt": when.isoformat(),
            "quote": post["quote"].strip(),
        })
        history.append({
            "time": dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "quote": post["quote"].strip(),
            "highlight": post.get("highlight"),
            "caption": post["caption"].strip(),
            "hashtags": tags,
            "posted": False,
            "queued_for": when.isoformat(),
        })
        main.log(f"Queued {item_id} ({dest.stat().st_size / 1048576:.1f} MB) for {when:%a %b %d %I:%M %p} Chicago")

    queue["items"].sort(key=lambda it: it["postAt"])
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    main.save_history(history)
    if main.OUTPUT.exists():
        main.OUTPUT.unlink()
    main.log(f"Done: {len(posts)} video(s) added. Queue now has {len(queue['items'])} item(s).")


if __name__ == "__main__":
    main_cli()
