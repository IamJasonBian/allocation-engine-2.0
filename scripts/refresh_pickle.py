#!/usr/bin/env python3
"""Generate a fresh Robinhood session pickle and upload it to Netlify Blobs.

Run locally:  python scripts/refresh_pickle.py

Uses a temporary pickle path so it does not overwrite your local session.
The static device token from Config is seeded automatically so the session
is created under the same device identity that Render uses.
"""

import os
import pickle
import sys
import tempfile

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robin_stocks.robinhood as rh
from app.config import Config

PICKLE_NAME = Config.RH_PICKLE_NAME
DEVICE_TOKEN = Config.RH_DEVICE_TOKEN

# Use a temp directory so we don't clobber the local session
PICKLE_DIR = tempfile.mkdtemp(prefix="rh_refresh_")
PICKLE_PATH = os.path.join(PICKLE_DIR, f"robinhood{PICKLE_NAME}.pickle")

NETLIFY_API_TOKEN = os.getenv("NETLIFY_API_TOKEN", "nfp_EJhNguVjnSF5dF2KnJjxPyU6Ghq9nsVE7201")
NETLIFY_SITE_ID = os.getenv("NETLIFY_SITE_ID", "3d014fc3-e919-4b4d-b374-e8606dee50df")
BLOB_URL = f"https://api.netlify.com/api/v1/blobs/{NETLIFY_SITE_ID}/rh-session/robinhood-pickle"


def login():
    """Login using the static device token and upload to Netlify."""
    # Seed the static device token so robin_stocks uses Render's device identity
    stub = {
        "device_token": DEVICE_TOKEN,
        "access_token": "",
        "token_type": "Bearer",
        "refresh_token": "",
    }
    with open(PICKLE_PATH, "wb") as f:
        pickle.dump(stub, f)
    print(f"Using device_token={DEVICE_TOKEN[:8]}...{DEVICE_TOKEN[-4:]}")

    env_email, env_password = Config.rh_credentials()
    email = env_email or input("Robinhood email: ").strip()
    password = env_password or input("Robinhood password: ").strip()

    mfa_code = None
    if Config.RH_TOTP_SECRET:
        import pyotp
        mfa_code = pyotp.TOTP(Config.RH_TOTP_SECRET).now()
        print(f"Using TOTP from env")

    print(f"Logging in as {email}...")
    result = rh.login(
        email, password,
        mfa_code=mfa_code,
        store_session=True,
        pickle_name=PICKLE_NAME,
        pickle_path=PICKLE_DIR,
    )

    if result:
        print(f"Login successful!")
        return True
    else:
        print("Login failed.")
        return False


def upload():
    """Upload the pickle to Netlify Blobs."""
    import requests

    if not os.path.isfile(PICKLE_PATH):
        print(f"No pickle found at {PICKLE_PATH}")
        return False

    with open(PICKLE_PATH, "rb") as f:
        data = pickle.load(f)
    print("\nPickle contents:")
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 8:
            print(f"  {k} = {v[:10]}...{v[-4:]}")
        else:
            print(f"  {k} = {v}")

    with open(PICKLE_PATH, "rb") as f:
        payload = f.read()

    resp = requests.put(
        BLOB_URL,
        headers={
            "Authorization": f"Bearer {NETLIFY_API_TOKEN}",
            "Content-Type": "application/octet-stream",
        },
        data=payload,
        timeout=15,
    )
    resp.raise_for_status()
    print(f"\nUploaded pickle to Netlify Blobs ({len(payload)} bytes)")
    return True


if __name__ == "__main__":
    if login():
        upload()
        print("\nDone! Restart the Render service to pick up the new session.")
    else:
        sys.exit(1)
