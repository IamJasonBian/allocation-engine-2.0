#!/usr/bin/env python3
"""Generate a request token for authenticating inbound calls.

Prints one cryptographically-strong bearer token. Set it as `[exec] token` in
env.prod; callers then send `Authorization: Bearer <token>` on privileged
endpoints (/login, /command, /orders/*, /exec). Rarely run — only to provision
or rotate the inbound auth token.

    ./venv/bin/python gen_token.py
"""

import secrets

if __name__ == "__main__":
    print(secrets.token_urlsafe(32))
