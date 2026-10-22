# HEM TikTok Pipeline — Setup

## Repo layout
```
322683.mp4                      ← your video template (repo root, exact name)
main.py                         ← the pipeline
fonts/                          ← free Google fonts used for the text styles (Open Font License)
tiktok_auth.py                  ← one-time TikTok login helper
requirements.txt
history.json                    ← past quotes (auto-updated every run)
.github/workflows/schedule.yml  ← 3x daily schedule
.github/workflows/tiktok_auth.yml ← one-time TikTok login workflow
```
GitHub's website only accepts uploads up to 25 MB. If 322683.mp4 is bigger, it has to be pushed with git.

## 1. Keep your own copies FIRST
GitHub Secrets are write-only — you can never read them back. Before pasting anything into GitHub,
save these in a password manager or a private note you control:
- Anthropic API key
- TikTok Client Key + Client Secret (also viewable anytime in the TikTok developer portal)
- GitHub personal access token (GH_PAT)

The TikTok refresh token does NOT need a copy — you can always get a new one by re-running the TikTok Auth workflow.

## 2. Secrets  (Repo → Settings → Secrets and variables → Actions → Secrets → New repository secret)
| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from console.anthropic.com → API Keys |
| `TIKTOK_CLIENT_KEY` | TikTok developer app → Client key |
| `TIKTOK_CLIENT_SECRET` | TikTok developer app → Client secret |
| `GH_PAT` | GitHub fine-grained token (see step 4) |
| `TIKTOK_REFRESH_TOKEN` | created automatically by step 5 — don't add by hand |

## 3. TikTok developer app (developers.tiktok.com)
1. Create an app. Add products **Login Kit** and **Content Posting API** (turn on **Direct Post** inside it).
2. Scopes: `user.info.basic`, `video.upload`, `video.publish`.
3. Login Kit → Redirect URI: any https address you control, e.g. `https://the-hybrid-elite-mafia.github.io/`.
   The page doesn't need to load — you only copy the address it lands on.
4. Submit the app for review. TikTok asks for a website, Terms of Service and Privacy Policy URL.

## 4. GitHub token (GH_PAT)
GitHub → Settings → Developer settings → Fine-grained tokens → Generate:
- Repository access: only this repo
- Permissions → **Secrets: Read and write**
- Expiration: up to 1 year. When it expires, make a new one and replace the `GH_PAT` secret.

This lets the pipeline save TikTok's rotated refresh token so login keeps working.

## 5. Connect TikTok (one time, works from your phone)
1. Repo → Settings → Secrets and variables → Actions → **Variables** → add `TIKTOK_REDIRECT_URI` = the exact URI from step 3.
2. Actions tab → **TikTok Auth (one-time)** → Run workflow, leave code empty.
3. Open the finished run → the summary shows a login link → tap it → approve.
4. Copy the full address your browser lands on. Codes expire in minutes.
5. Run **TikTok Auth** again, paste that address into **code**. Summary should say "Done ✅".

## 6. Optional variables (Variables tab — no code edits)
| Name | Default | What it does |
|---|---|---|
| `TIKTOK_POST_MODE` | `inbox` | `inbox` = video lands in TikTok inbox, you tap Post. `direct` = posts automatically |
| `TIKTOK_PRIVACY` | `PUBLIC_TO_EVERYONE` | used in direct mode |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Claude model |
| `TEXT_BOX_CENTER_X` / `_Y` | `0.5` / `0.345` | center of the glass display (fraction of frame) — set for the current template |
| `TEXT_BOX_WIDTH` / `_HEIGHT` | `0.58` / `0.34` | room the text may fill |
| `TEXT_COVER` | `0` | `1` = blur/darken the glass first (only for a template with old words baked in) |
| `TEXT_STYLE` | `neon` | text look: `neon`, `street`, `boss`, `stencil`, or `plain` |
| `TEXT_MAX_FONT` | set by style | biggest font as fraction of frame height (smaller number = smaller text) |

## 7. Test
Actions → **HEM TikTok Pipeline** → Run workflow. Download the `hem-post-N` artifact to watch output.mp4.
With only `ANTHROPIC_API_KEY` set, it renders and saves the video without posting — good first test.

## Known limits
- **Direct posting is private until TikTok audits your app.** Unaudited apps can only post SELF_ONLY,
  and TikTok's docs say the account itself must be private. That's why the default is `inbox` mode.
  After the audit passes, set `TIKTOK_POST_MODE` = `direct` for hands-off posting.
- GitHub cron runs in UTC and can start several minutes late when GitHub is busy.
- GitHub pauses scheduled workflows in public repos after 60 days without repo activity.
  The history commits *may* count as activity — not certain. If runs stop, the Actions tab shows an Enable button.
