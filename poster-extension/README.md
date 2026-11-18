# HEM TikTok Poster (Chrome add-on)

Loads the videos listed in `queue/queue.json` into TikTok Studio, types the caption and hashtags, picks the date and time, and schedules them. TikTok lets you schedule up to 10 days ahead, so the add-on schedules everything in the queue that falls inside that window.

## One-time install (on the laptop)
1. Download this repo folder `poster-extension` to the laptop (Claude puts a copy at `Downloads\HEM TikTok\poster-extension`).
2. In Chrome go to `chrome://extensions`, switch on **Developer mode** (top right).
3. Click **Load unpacked** and pick the `poster-extension` folder.
4. Pin the add-on (puzzle-piece icon → pin **HEM TikTok Poster**).
5. Make sure Chrome is signed in to the right TikTok account.

## Using it
- Click the add-on icon. Tick **Turned on**, choose a mode, press **Save**.
- **Review mode** (start here): it fills in one video completely and leaves the window open. Check it, then press TikTok's **Schedule** button yourself.
- **Auto mode**: it presses **Schedule** itself and moves to the next video.
- It checks the queue every hour while Chrome is open. **Run now** checks immediately.
- While it works, Chrome shows a bar saying the add-on "started debugging this browser". That is how it types into TikTok's caption box (TikTok's box rejects text pushed in any other way). Don't click **Cancel** on that bar while a video is being filled in.

## What each status means
- **WAITING** — in the queue, not done yet (or more than 10 days away).
- **FILLED** — review mode filled it in; you still press Schedule.
- **SCHEDULED** — TikTok accepted it.
- **FAILED** — something went wrong; the window is left open and the reason is in Activity. **Clear failed / review marks** lets it try again.
- **MISSED** — its post time had already passed.

## Known limits (honest)
- Times are set in the laptop's own time zone (TikTok's picker shows "Local Time"). The laptop is on Chicago time.
- TikTok's picker only has 5-minute steps, so post times are rounded down to a multiple of 5.
- If TikTok changes its upload page, the add-on will stop with FAILED and say which step broke.
- Chrome must be open and the laptop awake for the hourly check to run.

## Adding videos
Videos are built with `tools/make_batch.py` (no API key needed) and pushed to `queue/`. Claude does this for you.
