#!/usr/bin/env python3
"""
One-time TikTok login helper, run by the "TikTok Auth" workflow.

Run 1 (leave "code" empty): prints a TikTok login link in the run summary.
Run 2 (paste the code, or the whole redirect URL): trades it for a refresh
token and stores it in the TIKTOK_REFRESH_TOKEN GitHub secret.
"""

import os
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

SCOPES = "user.info.basic,video.upload,video.publish"


def summary(text):
    print(text)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main():
    client_key = os.environ["TIKTOK_CLIENT_KEY"].strip()
    client_secret = os.environ["TIKTOK_CLIENT_SECRET"].strip()
    redirect_uri = os.environ["TIKTOK_REDIRECT_URI"].strip()
    raw = (os.getenv("AUTH_CODE") or "").strip()

    if not raw:
        url = (
            "https://www.tiktok.com/v2/auth/authorize/"
            f"?client_key={quote(client_key)}"
            f"&scope={quote(SCOPES)}"
            "&response_type=code"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&state={secrets.token_urlsafe(12)}"
        )
        summary("## Step 1 — log in to TikTok\n")
        summary(f"[Tap here to authorize HEM Poster]({url})\n")
        summary("After you approve, your browser lands on your redirect page. Copy the WHOLE "
                "address from the address bar, then run this workflow again and paste it into **code**.")
        return

    # Accept either the bare code or the full redirect URL.
    if "code=" in raw:
        raw = parse_qs(urlparse(raw).query).get("code", [raw])[0]
    code = unquote(raw)

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    body = resp.json()
    if "refresh_token" not in body:
        summary(f"TikTok refused the code: {body.get('error')} — {body.get('error_description')}\n"
                "Codes expire within minutes and work once. Run Step 1 again for a fresh one.")
        sys.exit(1)

    scopes = body.get("scope", "")
    result = subprocess.run(
        ["gh", "secret", "set", "TIKTOK_REFRESH_TOKEN", "--repo", os.environ["GITHUB_REPOSITORY"]],
        input=body["refresh_token"], text=True, capture_output=True,
    )
    if result.returncode != 0:
        summary(f"Got the token but couldn't save it (check GH_PAT): {result.stderr.strip()}")
        sys.exit(1)

    days = int(body.get("refresh_expires_in", 0)) // 86400
    summary(f"## Done ✅\nTIKTOK_REFRESH_TOKEN saved. Granted scopes: `{scopes}`. "
            f"Valid about {days} days (the pipeline renews it automatically).")


if __name__ == "__main__":
    main()
