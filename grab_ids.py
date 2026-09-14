#!/usr/bin/env python3
"""Listen for messages and record every chat the bot can see.

Run it, then post any message in each channel/group. Each distinct chat is
printed once with its ID, title and type. Results are also appended to
data/grabbed_ids.txt so nothing is lost.

Usage:  .venv/bin/python grab_ids.py [seconds]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config

API = f"https://api.telegram.org/bot{config.BOT1_TOKEN}"
OUT = Path("data/grabbed_ids.txt")


def call(method: str, **params) -> dict:
    url = f"{API}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)


def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    if not config.BOT1_TOKEN:
        sys.exit("BOT1_TOKEN not set")

    me = call("getMe")["result"]
    print(f"listening as @{me['username']}  ({duration}s)")
    print("post any message in each channel/group now\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen: dict[int, dict] = {}
    offset = 0
    deadline = time.time() + duration

    while time.time() < deadline:
        try:
            # long-poll; keep timeout under the urlopen timeout
            data = call("getUpdates", offset=offset, timeout=25)
        except Exception as exc:
            print(f"  (poll error: {exc})")
            time.sleep(3)
            continue

        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            msg = (
                upd.get("message")
                or upd.get("channel_post")
                or upd.get("edited_message")
                or upd.get("edited_channel_post")
                or {}
            )
            chat = msg.get("chat")
            if not chat:
                continue
            cid = chat["id"]
            if cid in seen:
                continue

            seen[cid] = chat
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or "-"
            ctype = chat.get("type", "?")
            line = f"{cid}\t{ctype}\t{title}"
            print(f"  [{len(seen)}] {cid}   {ctype:10s} {title}")
            with OUT.open("a") as f:
                f.write(line + "\n")

    print(f"\ndone — {len(seen)} chat(s) captured, saved to {OUT}")
    if seen:
        print("\n--- paste-ready ---")
        for cid, chat in seen.items():
            title = chat.get("title") or "-"
            print(f"{title}  ->  {cid}")


if __name__ == "__main__":
    main()
