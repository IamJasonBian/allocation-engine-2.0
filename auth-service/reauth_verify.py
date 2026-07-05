#!/usr/bin/env python3
"""Clean re-login + prove the production token-reuse loop end-to-end.

  1. full login (device push — approve on your phone)  -> save
  2. refresh (spend refresh token, save the ROTATED one)
  3. refresh AGAIN using the rotated token (proves the save-on-rotate loop)
  4. live /accounts/ call with the final token (proves it actually works)

Leaves a valid, reusable session on disk. INFO level (no token dumps).
"""

import logging
import time

import config
import robinhood
import session as sm

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                    datefmt="%H:%M:%S")


def main():
    profile = config.DEFAULT_PROFILE
    creds = sm.load_credentials(profile)

    print("\n=== 1. full login (approve the push on your phone) ===")
    r = robinhood.authenticate(creds, approval_deadline=150)
    print(f"  login: {r.status} {r.error_code or ''}")
    if r.status != "OK":
        print("  >>> login failed; stopping.")
        return
    sm.save(profile, r.session)
    a0 = r.session.access_token

    print("\n=== 2. refresh -> save rotated token ===")
    r1 = robinhood.refresh(sm.load(profile))
    print(f"  refresh#1: {r1.status} {r1.error_code or ''}; new access differs: "
          f"{r1.session.access_token != a0 if r1.session else 'n/a'}")
    if r1.status != "OK":
        print("  >>> refresh#1 failed; stopping."); return
    sm.save(profile, r1.session)

    print("\n=== 3. refresh AGAIN with the rotated token (the real reuse loop) ===")
    r2 = robinhood.refresh(sm.load(profile))
    print(f"  refresh#2: {r2.status} {r2.error_code or ''}; new access differs: "
          f"{r2.session.access_token != r1.session.access_token if r2.session else 'n/a'}")
    if r2.status != "OK":
        print("  >>> refresh#2 failed; stopping."); return
    sm.save(profile, r2.session)

    print("\n=== 4. live /accounts/ with the final token ===")
    acct = robinhood.get_account(sm.load(profile))
    print(f"  live account: {acct.get('account_number')}")

    print("\n=== token-reuse loop verified; clean session saved ===")
    print(f"  status(): {sm.status(profile)}")


if __name__ == "__main__":
    main()
