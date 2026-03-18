#!/usr/bin/env python3
"""Generate a fresh Robinhood session pickle and upload it to Netlify Blobs.

Run locally:  python scripts/refresh_pickle.py

This will prompt for your Robinhood credentials interactively,
then upload the session pickle so Render can use it.
"""

import os
import pickle
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robin_stocks.robinhood as rh

PICKLE_NAME = os.getenv("RH_PICKLE_NAME", "taipei_session")
HOME = os.path.expanduser("~")
PICKLE_PATH = os.path.join(HOME, ".tokens", f"robinhood{PICKLE_NAME}.pickle")

# Netlify Blobs config — set these or they'll be read from env
NETLIFY_API_TOKEN = os.getenv("NETLIFY_API_TOKEN", "nfp_EJhNguVjnSF5dF2KnJjxPyU6Ghq9nsVE7201")
NETLIFY_SITE_ID = os.getenv("NETLIFY_SITE_ID", "3d014fc3-e919-4b4d-b374-e8606dee50df")
BLOB_URL = f"https://api.netlify.com/api/v1/blobs/{NETLIFY_SITE_ID}/rh-session/robinhood-pickle"


def login():
    """Interactive Robinhood login — will prompt for username/password/MFA."""
    email = input("Robinhood email: ").strip()
    password = input("Robinhood password: ").strip()

    print("\nLogging in...")
    result = rh.login(
        email, password,
        store_session=True,
        pickle_name=PICKLE_NAME,
    )

    if result:
        print(f"Login successful! Pickle saved to {PICKLE_PATH}")
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

    # Show what's in the pickle
    with open(PICKLE_PATH, "rb") as f:
        data = pickle.load(f)
    print("\nPickle contents:")
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 8:
            print(f"  {k} = {v[:10]}...{v[-4:]}")
        else:
            print(f"  {k} = {v}")

    # Upload
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
