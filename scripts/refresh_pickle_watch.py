#!/usr/bin/env python3
"""Bounded-retry wrapper around scripts/refresh_pickle.py.

Robinhood's login endpoint will return 429 if hammered (see CLAUDE.md
"429 rate limit storm"). This wrapper attempts the refresh up to 5 times
with exponential backoff, pings Slack on failures, and — when run from a
TTY — prompts the user to re-arm the retry budget once it's exhausted so
they can resume after Robinhood cools off without re-entering credentials.

Run locally:  python scripts/refresh_pickle_watch.py
"""

import importlib.util
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from app.config import Config
from app.slack import notify as slack_notify

_RP_PATH = os.path.join(os.path.dirname(__file__), "refresh_pickle.py")
_spec = importlib.util.spec_from_file_location("refresh_pickle", _RP_PATH)
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [30, 60, 120, 240, 480]


def resolve_credentials() -> None:
    """Populate Config with email/password so rp.login() does not prompt
    inside the retry loop."""
    email, password = Config.rh_credentials()
    if not email:
        email = input("Robinhood email: ").strip()
    if not password:
        password = input("Robinhood password: ").strip()
    if Config.RH_ACTIVE_ACCOUNT == "automated":
        Config.RH_AUTO_EMAIL = email
        Config.RH_AUTO_PASSWORD = password
    else:
        Config.RH_MAIN_EMAIL = email
        Config.RH_MAIN_PASSWORD = password


def attempt_with_backoff() -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n[refresh_pickle_watch] Attempt {attempt}/{MAX_ATTEMPTS}")
        try:
            ok = rp.login()
        except Exception as exc:
            print(f"[refresh_pickle_watch] login() raised: {exc}")
            ok = False

        if ok:
            rp.upload()
            slack_notify(
                f":white_check_mark: refresh_pickle_watch — pickle refreshed "
                f"on attempt {attempt}/{MAX_ATTEMPTS}"
            )
            return True

        if attempt < MAX_ATTEMPTS:
            delay = BACKOFF_SECONDS[attempt - 1]
            slack_notify(
                f":warning: refresh_pickle_watch — login failed "
                f"({attempt}/{MAX_ATTEMPTS}); sleeping {delay}s before retry"
            )
            print(f"[refresh_pickle_watch] Sleeping {delay}s before retry...")
            time.sleep(delay)
    return False


def main() -> int:
    resolve_credentials()
    while True:
        if attempt_with_backoff():
            print("\nDone! Restart the Render service to pick up the new session.")
            return 0
        slack_notify(
            f"<!channel> :rotating_light: refresh_pickle_watch — "
            f"exhausted {MAX_ATTEMPTS} login attempts; backing off"
        )
        if not sys.stdin.isatty():
            print("[refresh_pickle_watch] Non-interactive — exiting.")
            return 1
        try:
            input(
                "\n[refresh_pickle_watch] Press Enter to re-arm the retry "
                "budget, or Ctrl+C to abort: "
            )
        except (KeyboardInterrupt, EOFError):
            print("\n[refresh_pickle_watch] Aborted.")
            return 1


if __name__ == "__main__":
    sys.exit(main())
