#!/usr/bin/env python3
"""
Hybrid Elite Mafia — automated TikTok pipeline.

Runs ONCE, top to bottom, per GitHub Actions trigger (no while-loops):
  1. Load history.json (past quotes) so every run stays fresh.
  2. Ask Claude for a JSON package: quote (<15 words), caption, 4-5 hashtags.
  3. Use FFmpeg to burn the quote (white, bold, word-wrapped, centered)
     into the glass display of 322683.mp4 -> output.mp4.
  4. Send output.mp4 to TikTok through the official Content Posting API.
  5. Save the new quote to history.json (the workflow commits it back).
"""

import datetime
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import requests
from PIL import ImageFont

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "322683.mp4"
OUTPUT = ROOT / "output.mp4"
HISTORY = ROOT / "history.json"
BUILD = ROOT / "build"
POST_META = BUILD / "post.json"
FONT = os.getenv("FONT_PATH") or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def env_float(name, default):
    """Read a float from the environment. Empty/unset -> default."""
    raw = (os.getenv(name) or "").strip()
    return float(raw) if raw else default


# --------------------------------------------------------------------------
# Settings (override with GitHub repository *Variables*, no code edits needed)
# --------------------------------------------------------------------------
# Claude 3.5 Sonnet has been retired from the API, so the default is the
# current Sonnet model. Set the ANTHROPIC_MODEL variable to change it.
MODEL = (os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5-5").strip()

# Where the glass display sits in the frame, as fractions of width/height.
# Defaults are measured from the 720x1280 HEM template: the glass panel is
# centered horizontally and sits about a third of the way down the frame.
BOX_CENTER_X = env_float("TEXT_BOX_CENTER_X", 0.50)
BOX_CENTER_Y = env_float("TEXT_BOX_CENTER_Y", 0.345)
BOX_WIDTH = env_float("TEXT_BOX_WIDTH", 0.58)
BOX_HEIGHT = env_float("TEXT_BOX_HEIGHT", 0.34)

# Optional: blur + darken the inside of the glass (soft edges) before adding text.
# Only needed if a template has old words baked into the glass. The current
# 322683.mp4 is blank, so this is OFF. Set the TEXT_COVER variable to 1 to turn it on.
COVER = (os.getenv("TEXT_COVER") or "0").strip() not in ("0", "false", "no", "off")
COVER_X = env_float("COVER_X", 0.08)       # left edge (fraction of width)
COVER_Y = env_float("COVER_Y", 0.12)       # top edge (fraction of height)
COVER_W = env_float("COVER_WIDTH", 0.84)
COVER_H = env_float("COVER_HEIGHT", 0.50)
COVER_DARKEN = env_float("COVER_DARKEN", 0.32)

# "inbox"  = video lands in your TikTok inbox/drafts, you tap Post (works before audit)
# "direct" = posts straight to the profile (needs TikTok's audit for public posts)
POST_MODE = (os.getenv("TIKTOK_POST_MODE") or "inbox").strip().lower()
PRIVACY = (os.getenv("TIKTOK_PRIVACY") or "PUBLIC_TO_EVERYONE").strip().upper()

HISTORY_KEEP = 300      # quotes kept in history.json
HISTORY_IN_PROMPT = 60  # recent quotes shown to Claude as "do not repeat"
MAX_ATTEMPTS = 4        # Claude retries if the output breaks the rules

TIKTOK_API = "https://open.tiktokapis.com"
LOCAL_TZ = ZoneInfo("America/Chicago")

THEMES = [
    "survival when nobody is coming to save you",
    "loyalty that gets tested and holds",
    "overcoming odds stacked against you",
    "grinding in silence while others sleep",
    "turning pain into fuel",
    "keeping your circle small and solid",
    "earning respect instead of asking for it",
    "rising after being counted out",
    "staying real when it costs you",
    "family over everything",
    "discipline beating talent",
    "scars as proof you made it through",
]


def log(msg):
    print(f"[HEM] {msg}", flush=True)


# --------------------------------------------------------------------------
# History (keeps quotes non-repetitive across runs)
# --------------------------------------------------------------------------
def load_history():
    if not HISTORY.exists():
        return []
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        log("history.json was unreadable — starting a fresh history.")
        return []


def save_history(history):
    HISTORY.write_text(
        json.dumps(history[-HISTORY_KEEP:], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def normalize(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).split()


def too_similar(quote, history):
    """Reject exact repeats and near-duplicates (70%+ word overlap)."""
    new_words = set(normalize(quote))
    if not new_words:
        return True
    for item in history:
        old_words = set(normalize(item.get("quote", "")))
        if not old_words:
            continue
        overlap = len(new_words & old_words) / len(new_words | old_words)
        if overlap >= 0.7:
            return True
    return False


# --------------------------------------------------------------------------
# Step 1: Claude generates the content package
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You write for "Hybrid Elite Mafia" (HEM), an independent music and lifestyle brand.
Voice: gritty, resilient, thought-provoking, motivational. Themes: survival, loyalty, overcoming odds,
grinding, staying solid. Street-smart and grown, never corny, never cliché, no glorifying violence or crime.

Return ONLY a JSON object, no markdown, no commentary, in exactly this shape:
{"quote": "...", "highlight": "...", "caption": "...", "hashtags": ["#HybridEliteMafia", "#...", "#...", "#..."]}

Rules:
- quote: an original line, 6 to 14 words (strictly under 15). No hashtags, no emojis, no quotation marks.
- highlight: the single most powerful word from the quote, copied exactly (it gets lit up in gold).
- caption: one short line (under 100 characters) that frames the quote. No hashtags in the caption.
- hashtags: 4 or 5 total, the first must be #HybridEliteMafia, the rest relevant and popular on TikTok.
"""


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in Claude's reply")
    return json.loads(text[start:end + 1])


def clean_package(data, history):
    """Validate Claude's JSON and enforce every content rule. Raises on failure."""
    quote = str(data.get("quote", "")).strip().strip('"“”').strip()
    quote = re.sub(r"\s+", " ", re.sub(r"#\w+", "", quote)).strip()
    word_count = len(quote.split())
    if word_count == 0 or word_count >= 15:
        raise ValueError(f"quote has {word_count} words (must be 1-14)")
    if too_similar(quote, history):
        raise ValueError("quote repeats or is too close to an earlier one")

    caption = re.sub(r"#\w+", "", str(data.get("caption", ""))).strip()
    caption = re.sub(r"\s+", " ", caption)
    if not caption:
        raise ValueError("empty caption")
    if len(caption) > 150:
        raise ValueError("caption too long")

    raw_tags = data.get("hashtags", [])
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split()
    tags = []
    for tag in raw_tags:
        tag = re.sub(r"[^A-Za-z0-9_]", "", str(tag))
        if tag and tag.lower() != "hybridelitemafia" and f"#{tag}".lower() not in [t.lower() for t in tags]:
            tags.append(f"#{tag}")
    tags = ["#HybridEliteMafia"] + tags[:4]
    if len(tags) < 4:
        raise ValueError(f"only {len(tags)} hashtags (need 4-5)")

    highlight = clean_word(str(data.get("highlight", "")).split()[0] if str(data.get("highlight", "")).split() else "")
    if highlight not in [clean_word(w) for w in quote.split()]:
        highlight = clean_word(quote.split()[-1])  # fallback: last word

    return {"quote": quote, "highlight": highlight, "caption": caption, "hashtags": tags}


def generate_content(history):
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    now = datetime.datetime.now(LOCAL_TZ)
    slot = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "night"
    theme = THEMES[(now.toordinal() * 3 + now.hour) % len(THEMES)]
    recent = [h["quote"] for h in history[-HISTORY_IN_PROMPT:] if h.get("quote")]
    banned = "\n".join(f"- {q}" for q in recent) or "- (none yet)"

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_msg = (
            f"This is the {slot} post. Lean into this theme: {theme}.\n"
            f"Do NOT repeat, remix, or paraphrase any of these earlier quotes:\n{banned}\n"
        )
        if last_error:
            user_msg += f"\nYour last answer was rejected because: {last_error}. Fix that.\n"

        log(f"Asking {MODEL} for content (attempt {attempt}/{MAX_ATTEMPTS})...")
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(getattr(block, "text", "") for block in response.content)
        try:
            package = clean_package(extract_json(text), history)
            log(f"Quote: {package['quote']}")
            return package
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            log(f"Rejected: {last_error}")

    raise RuntimeError(f"Claude failed the content rules {MAX_ATTEMPTS} times: {last_error}")


# --------------------------------------------------------------------------
# Step 2: FFmpeg render
# --------------------------------------------------------------------------
def probe_video(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    info = json.loads(out)
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(info["format"]["duration"])


STYLES = {
    # Tall condensed caps, white with a cyan neon glow, key word in gold.
    "neon": {"font": "BebasNeue-Regular.ttf", "max_font": 0.064, "spacing": 1.02,
             "color": "0xFFFFFF", "glow": "0x29D9FF", "hl": "0xFFB238", "hl_glow": "0xFF7A00",
             "glow_wide": 26, "glow_tight": 6},
    # Heavy poster caps, softer glow.
    "street": {"font": "Anton-Regular.ttf", "max_font": 0.056, "spacing": 1.12,
               "color": "0xFFFFFF", "glow": "0x29D9FF", "hl": "0xFFC14D", "hl_glow": "0xFF8A00",
               "glow_wide": 22, "glow_tight": 5},
    # Squared-off techy caps.
    "boss": {"font": "RussoOne-Regular.ttf", "max_font": 0.046, "spacing": 1.22,
             "color": "0xFFFFFF", "glow": "0x29D9FF", "hl": "0xFFB238", "hl_glow": "0xFF7A00",
             "glow_wide": 22, "glow_tight": 5},
    # Military stencil caps.
    "stencil": {"font": "BlackOpsOne-Regular.ttf", "max_font": 0.046, "spacing": 1.22,
                "color": "0xFFFFFF", "glow": "0x29D9FF", "hl": "0xFFB238", "hl_glow": "0xFF7A00",
                "glow_wide": 22, "glow_tight": 5},
    # The original plain look (system bold font, dark outline, no glow).
    "plain": {"font": None, "max_font": 0.075, "spacing": 1.22,
              "color": "0xFFFFFF", "glow": None, "hl": "0xFFFFFF", "hl_glow": None,
              "glow_wide": 0, "glow_tight": 0},
}
STYLE_NAME = (os.getenv("TEXT_STYLE") or "neon").strip().lower()
FONTS_DIR = ROOT / "fonts"


def get_style(name=None):
    name = (name or STYLE_NAME).lower()
    if name not in STYLES:
        log(f"Unknown TEXT_STYLE '{name}' — using neon.")
        name = "neon"
    style = dict(STYLES[name])
    style["name"] = name
    style["font_path"] = str(FONTS_DIR / style["font"]) if style["font"] else FONT
    if not Path(style["font_path"]).exists():
        raise FileNotFoundError(f"Font missing: {style['font_path']}")
    return style


def clean_word(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def layout_text(quote, vid_w, vid_h, style):
    """Pick the line wrap + font size that fills the glass box as large as possible
    (up to the style's cap). Widths are measured with the real font file."""
    box_w, box_h = vid_w * BOX_WIDTH, vid_h * BOX_HEIGHT
    max_font = vid_h * env_float("TEXT_MAX_FONT", style["max_font"])
    ref = ImageFont.truetype(style["font_path"], 100)
    best = None
    for width in range(6, 41):
        lines = textwrap.wrap(quote, width=width, break_long_words=False, break_on_hyphens=False)
        if not lines:
            continue
        widest = max(ref.getlength(line) for line in lines) / 100.0
        size = min(box_w / widest, box_h / (len(lines) * style["spacing"]), max_font)
        # Prefer balanced blocks of 2-4 lines when sizes are close.
        key = (round(size / 3), -abs(len(lines) - 3), size)
        if best is None or key > best[0]:
            best = (key, lines, int(size))
    return best[1], best[2], ref


def segment_lines(lines, highlight, ref, size, center_x):
    """Split each line into runs of normal / highlighted words with exact x positions."""
    target = clean_word(highlight or "")
    if not target or not any(clean_word(w) == target for line in lines for w in line.split()):
        target = clean_word(lines[-1].split()[-1])  # fall back to the last word
    used = False
    out = []
    scale = size / 100.0
    for line in lines:
        words = line.split(" ")
        x0 = center_x - ref.getlength(line) * scale / 2
        runs, current, cur_hl, start = [], [], None, 0
        pos = 0
        for word in words:
            is_hl = not used and clean_word(word) == target
            if is_hl:
                used = True
            if cur_hl is None:
                cur_hl, start = is_hl, pos
            if is_hl != cur_hl:
                runs.append((" ".join(current), cur_hl, start))
                current, cur_hl, start = [], is_hl, pos
            current.append(word)
            pos += len(word) + 1
        runs.append((" ".join(current), cur_hl, start))
        out.append([(text, hl, x0 + ref.getlength(line[:st]) * scale) for text, hl, st in runs if text])
    return out


def ffmpeg_escape(value):
    return str(value).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_video(quote, highlight=None, style_name=None):
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"{TEMPLATE.name} is missing from the repo root")
    BUILD.mkdir(exist_ok=True)
    style = get_style(style_name)
    src_w, src_h, duration = probe_video(TEMPLATE)
    # Render at 1080x1920 (TikTok's full quality). Text is drawn AFTER the
    # upscale, so letters stay razor sharp even though the template is 720p.
    scale = max(1.0, 1080 / src_w)
    vid_w, vid_h = int(round(src_w * scale / 2)) * 2, int(round(src_h * scale / 2)) * 2
    text = quote.upper() if style["font"] else quote
    lines, size, ref = layout_text(text, vid_w, vid_h, style)
    log(f"Template {vid_w}x{vid_h}, {duration:.1f}s — style '{style['name']}', "
        f"{len(lines)} line(s) at font size {size}")

    line_h = size * style["spacing"]
    block_h = line_h * (len(lines) - 1) + size
    top = vid_h * BOX_CENTER_Y - block_h / 2
    runs = segment_lines(lines, highlight, ref, size, vid_w * BOX_CENTER_X)

    def drawtexts(layer):
        """layer = 'text' (sharp letters) or 'glow' (colored copy that gets blurred)."""
        parts = []
        n = 0
        for i, line_runs in enumerate(runs):
            for run_text, is_hl, x in line_runs:
                f = BUILD / f"run_{n}.txt"
                n += 1
                # Text lives in files: no escaping trouble with ' : % in quotes.
                f.write_text(run_text, encoding="utf-8")
                if layer == "glow":
                    color = style["hl_glow"] if is_hl else style["glow"]
                    extra = f"borderw={max(2, size // 18)}:bordercolor={color}:"
                elif style["glow"] is None:
                    color = style["color"]
                    extra = (f"borderw={max(2, size // 16)}:bordercolor=black@0.55:"
                             f"shadowx=0:shadowy={max(2, size // 14)}:shadowcolor=black@0.45:")
                else:
                    color = style["hl"] if is_hl else style["color"]
                    extra = (f"borderw={max(1, size // 45)}:bordercolor=0x06121A@0.85:"
                             f"shadowx=0:shadowy={max(2, size // 25)}:shadowcolor=black@0.55:")
                parts.append(
                    "drawtext="
                    f"fontfile='{ffmpeg_escape(style['font_path'])}':"
                    f"textfile='{ffmpeg_escape(f)}':expansion=none:"
                    f"fontcolor={color}:fontsize={size}:{extra}"
                    f"x={x:.1f}:y={top + i * line_h:.1f}"
                )
        return ",".join(parts)

    graph = [f"[0:v]scale={vid_w}:{vid_h}:flags=lanczos,setsar=1[src0]"]
    current = "[src0]"
    if COVER:
        # Soft-edged frosted panel over old baked-in text (only for such templates).
        cx, cy = int(vid_w * COVER_X), int(vid_h * COVER_Y)
        cw, ch = int(vid_w * COVER_W) // 2 * 2, int(vid_h * COVER_H) // 2 * 2
        feather = max(8, cw // 12)
        graph.append(
            f"[src0]split[base][src];"
            f"[src]crop={cw}:{ch}:{cx}:{cy},gblur=sigma={max(20, cw // 14)},"
            f"eq=brightness=-{COVER_DARKEN}:saturation=0.8,format=rgba[blur];"
            f"color=c=black:s={cw}x{ch},format=gray,"
            f"drawbox=x={feather}:y={feather}:w={cw - 2 * feather}:h={ch - 2 * feather}:color=white:t=fill,"
            f"gblur=sigma={feather / 2:.1f}[mask];"
            f"[blur][mask]alphamerge[panel];"
            f"[base][panel]overlay={cx}:{cy}:shortest=1[bg]"
        )
        current = "[bg]"

    if style["glow"] is None:
        graph.append(f"{current}{drawtexts('text')},format=yuv420p[out]")
    else:
        # Transparent layers the same size/length as the video, then:
        # wide soft glow + tight glow + sharp letters on top = neon sign look.
        graph.append(
            f"{current}split=3[v][g][t];"
            f"[g]format=rgba,colorchannelmixer=aa=0,{drawtexts('glow')},split[gsrc1][gsrc2];"
            f"[gsrc1]gblur=sigma={style['glow_wide']},split[gw][gw2];"
            f"[gsrc2]gblur=sigma={style['glow_tight']}[gt];"
            f"[t]format=rgba,colorchannelmixer=aa=0,{drawtexts('text')}[txt];"
            f"[v][gw]overlay=format=auto[v0];"
            f"[v0][gw2]overlay=format=auto[v1];"
            f"[v1][gt]overlay=format=auto[v2];"
            f"[v2][txt]overlay=format=auto,format=yuv420p[out]"
        )

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(TEMPLATE),
        "-filter_complex", ";".join(graph),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(OUTPUT),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-2000:]}")
    log(f"Rendered {OUTPUT.name} ({OUTPUT.stat().st_size / 1_048_576:.1f} MB)")
    return duration


# --------------------------------------------------------------------------
# Step 3: TikTok Content Posting API
# --------------------------------------------------------------------------
def tiktok_data(resp, step):
    """Unwrap a TikTok API response or raise a readable error."""
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"{step}: HTTP {resp.status_code}, not JSON: {resp.text[:300]}")
    err = body.get("error") or {}
    if resp.status_code != 200 or err.get("code") not in (None, "", "ok"):
        raise RuntimeError(
            f"{step} failed: HTTP {resp.status_code} code={err.get('code')} "
            f"message={err.get('message')} log_id={err.get('log_id')}"
        )
    return body.get("data") or {}


def save_refresh_token(token):
    """TikTok may hand back a NEW refresh token. Store it in the GitHub secret
    so the next run still works. Needs the GH_PAT secret (exposed as GH_TOKEN)."""
    repo = os.getenv("GITHUB_REPOSITORY")
    if not os.getenv("GH_TOKEN") or not repo:
        log("WARNING: TikTok rotated the refresh token but GH_PAT isn't set, so it "
            "couldn't be saved. If the next run fails auth, re-run the TikTok Auth workflow.")
        return
    result = subprocess.run(
        ["gh", "secret", "set", "TIKTOK_REFRESH_TOKEN", "--repo", repo],
        input=token, text=True, capture_output=True,
    )
    if result.returncode != 0:
        log(f"WARNING: couldn't update TIKTOK_REFRESH_TOKEN: {result.stderr.strip()}")
    else:
        log("Saved the rotated TikTok refresh token.")


def tiktok_access_token():
    old_refresh = os.environ["TIKTOK_REFRESH_TOKEN"].strip()
    resp = requests.post(
        f"{TIKTOK_API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"].strip(),
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"].strip(),
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
        },
        timeout=30,
    )
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(
            f"TikTok token refresh failed: {body.get('error')} {body.get('error_description')} "
            "— re-run the TikTok Auth workflow to get a new refresh token."
        )
    new_refresh = body.get("refresh_token")
    if new_refresh and new_refresh != old_refresh:
        save_refresh_token(new_refresh)
    return body["access_token"]


def chunk_plan(size):
    """TikTok chunk rules: one chunk if the file is <= 64 MB, otherwise 10 MB chunks
    with the remainder folded into the last chunk."""
    if size <= 64 * 1024 * 1024:
        return size, 1
    chunk = 10 * 1024 * 1024
    return chunk, size // chunk


def upload_chunks(upload_url, size, chunk_size, total):
    with open(OUTPUT, "rb") as fh:
        for i in range(total):
            start = i * chunk_size
            end = size - 1 if i == total - 1 else start + chunk_size - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            resp = requests.put(
                upload_url,
                data=data,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                },
                timeout=300,
            )
            if resp.status_code not in (200, 201, 206):
                raise RuntimeError(f"Chunk {i + 1}/{total} upload failed: HTTP {resp.status_code} {resp.text[:300]}")
            log(f"Uploaded chunk {i + 1}/{total}")


def post_to_tiktok(package, duration):
    token = tiktok_access_token()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    size = OUTPUT.stat().st_size
    chunk_size, total = chunk_plan(size)
    source_info = {"source": "FILE_UPLOAD", "video_size": size,
                   "chunk_size": chunk_size, "total_chunk_count": total}
    description = f"{package['caption']}\n\n{' '.join(package['hashtags'])}"

    if POST_MODE == "direct":
        creator = tiktok_data(
            requests.post(f"{TIKTOK_API}/v2/post/publish/creator_info/query/", headers=auth, timeout=30),
            "creator_info",
        )
        options = creator.get("privacy_level_options") or []
        privacy = PRIVACY if PRIVACY in options else ("SELF_ONLY" if "SELF_ONLY" in options else options[0])
        if privacy != PRIVACY:
            log(f"Privacy {PRIVACY} not allowed for this account/app right now — using {privacy}.")
        max_len = creator.get("max_video_post_duration_sec")
        if max_len and duration > max_len:
            raise RuntimeError(f"Video is {duration:.0f}s but this account allows {max_len}s max.")
        init = tiktok_data(
            requests.post(
                f"{TIKTOK_API}/v2/post/publish/video/init/",
                headers=auth,
                json={
                    "post_info": {
                        "title": description,
                        "privacy_level": privacy,
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": source_info,
                },
                timeout=30,
            ),
            "direct post init",
        )
    else:
        init = tiktok_data(
            requests.post(
                f"{TIKTOK_API}/v2/post/publish/inbox/video/init/",
                headers=auth,
                json={"source_info": source_info},
                timeout=30,
            ),
            "inbox upload init",
        )

    publish_id = init["publish_id"]
    upload_chunks(init["upload_url"], size, chunk_size, total)

    # Check processing status a limited number of times (bounded for-loop).
    for _ in range(30):
        time.sleep(10)
        status = tiktok_data(
            requests.post(f"{TIKTOK_API}/v2/post/publish/status/fetch/", headers=auth,
                          json={"publish_id": publish_id}, timeout=30),
            "status fetch",
        )
        state = status.get("status")
        log(f"TikTok status: {state}")
        if state == "PUBLISH_COMPLETE":
            log("Posted to TikTok.")
            return publish_id
        if state == "SEND_TO_USER_INBOX":
            log("Sent to your TikTok inbox — open TikTok and tap the notification to post. "
                f"Caption to paste:\n{description}")
            return publish_id
        if state == "FAILED":
            raise RuntimeError(f"TikTok rejected the video: {status.get('fail_reason')}")
    log("TikTok is still processing after 5 minutes — check the app shortly.")
    return publish_id


# --------------------------------------------------------------------------
# Main — runs once, top to bottom
# --------------------------------------------------------------------------
def main():
    log(f"Run started {datetime.datetime.now(LOCAL_TZ):%Y-%m-%d %I:%M %p %Z}")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY secret is not set")

    history = load_history()
    package = generate_content(history)
    duration = render_video(package["quote"], package["highlight"])

    BUILD.mkdir(exist_ok=True)
    POST_META.write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")

    record = {
        "time": datetime.datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        **package,
        "posted": False,
    }
    # Save the quote before posting, so it never gets reused even if the upload fails.
    history.append(record)
    save_history(history)

    tiktok_ready = all(os.getenv(k) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"))
    if not tiktok_ready:
        log("TikTok secrets not set — video rendered and saved as a workflow artifact only.")
        return

    record["publish_id"] = post_to_tiktok(package, duration)
    record["posted"] = True
    save_history(history)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail the GitHub Action so you get an email
        log(f"FAILED: {exc}")
        sys.exit(1)
