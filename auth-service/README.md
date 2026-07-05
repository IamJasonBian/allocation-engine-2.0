# auth-service

A tiny standalone service that authenticates to Robinhood (credentials from GCP
Secret Manager) and exposes an HTTP surface other services call to run commands
inside the authenticated session and relay status/error codes.

Lightweight by design (targets an **e2-micro**, 1 GB RAM):
- Web layer is the Python **stdlib `http.server`** — no Flask/gunicorn.
- Secrets via the **Secret Manager REST API** + the GCE metadata token — no
  `google-cloud-secret-manager` gRPC stack.
- Config is stdlib **`configparser`** — no `python-dotenv`.
- Two pip deps total: `requests`, `pyotp`.

---

## GCP prerequisites

These must exist **before** deploying. We hit each of these the hard way, so
they are called out explicitly.

1. **Secret Manager secrets** holding the Robinhood credentials, e.g.
   `rh-prod-user` (username) and `rh-prod-pass` (password):
   ```bash
   printf 'you@example.com' | gcloud secrets create rh-prod-user --data-file=- --project PROJECT
   printf 'yourpassword'    | gcloud secrets create rh-prod-pass --data-file=- --project PROJECT
   ```
   (Optional `rh-prod-totp` if the login uses a TOTP secret.)

2. **Secret Manager API enabled**:
   ```bash
   gcloud services enable secretmanager.googleapis.com --project PROJECT
   ```

3. **VM access scope must include `cloud-platform`.** The legacy default scopes
   do **not** allow Secret Manager, and the metadata token is scope-limited
   regardless of IAM — this shows up as a `403` on secret access. Fix (VM must
   be stopped to change scopes):
   ```bash
   gcloud compute instances stop VM --zone ZONE --project PROJECT
   gcloud compute instances set-service-account VM --zone ZONE --project PROJECT \
     --service-account SA_EMAIL --scopes cloud-platform
   gcloud compute instances start VM --zone ZONE --project PROJECT
   ```

4. **IAM: `secretmanager.secretAccessor` on the VM's service account.** Basic
   `roles/editor` does **not** grant access to secret payloads. Grant per-secret
   (least privilege):
   ```bash
   for S in rh-prod-user rh-prod-pass; do
     gcloud secrets add-iam-policy-binding $S --project PROJECT \
       --member serviceAccount:SA_EMAIL --role roles/secretmanager.secretAccessor
   done
   ```

5. **Static external IP.** A changing source IP re-trips Robinhood's device
   challenge on every login. Pin the VM's IP:
   ```bash
   gcloud compute addresses create VM-ip --project PROJECT --region REGION \
     --addresses $(gcloud compute instances describe VM --zone ZONE --project PROJECT \
       --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
   ```

6. **Firewall: keep port 8080 internal.** The server binds `0.0.0.0:8080` and
   `/exec` runs arbitrary commands — do not expose it to the internet. Reach it
   over the VPC/SSH only, and set a real `[exec] token` (see below).

---

## Deploy

SSH to the VM, then:

```bash
# 1. get the code
git clone https://github.com/IamJasonBian/allocation-engine-2.0.git
cd allocation-engine-2.0/auth-service      # or curl install.sh (see below)

# 2. venv + deps  (installs python3-venv if missing)
./install.sh

# 3. configure
cp env.prod.example env.prod && chmod 600 env.prod
#   edit env.prod:
#     [gcp] project_id      = PROJECT   (blank = auto-detect on GCE)
#     [rh.auth] user/pass   = secret NAMES (rh-prod-user / rh-prod-pass)
#     [rh.auth] device      = a fixed UUID (pin it once approved, keeps device stable)
#     [exec] token          = a strong bearer token for privileged endpoints

# 4. install + start the systemd service
sed "s#__INSTALL_DIR__#$PWD#g; s#__USER__#$USER#g" auth-service.service \
  | sudo tee /etc/systemd/system/auth-service.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now auth-service

# 5. first login — fires a device-approval push; approve it on your phone
curl -s -X POST localhost:8080/login -H "Authorization: Bearer <exec-token>"
```

Curl-style install (fetches sources, makes venv, drops an `env.prod`):
```bash
curl -fsSL https://raw.githubusercontent.com/IamJasonBian/allocation-engine-2.0/main/auth-service/install.sh | bash
```

### First-login / device approval

`/login` drives Robinhood's device-approval workflow and **blocks up to ~3 min**
waiting for you to tap **Approve** in the Robinhood app. Once it succeeds, the
access + refresh tokens are cached (`state/`, mode 600) and reused; the service
silently refreshes before expiry, so you should not be prompted again unless the
whole token chain is invalidated. Keeping the IP and `[rh.auth] device` stable is
what prevents repeat challenges.

---

## Endpoints

All POSTs and order reads require `Authorization: Bearer <exec-token>`.

| Method | Path                               | Purpose                                   |
|--------|------------------------------------|-------------------------------------------|
| GET    | `/health`                          | liveness                                  |
| GET    | `/auth/status`                     | auth state + error codes (for alerting)   |
| POST   | `/login`                           | force a real (re)authentication           |
| POST   | `/command`                         | generic authenticated intake for callers  |
| GET    | `/orders/trailing_stop`            | active percentage trailing-stop orders    |
| POST   | `/orders/trailing_stop`            | relay a place payload (`dry_run` default) |
| POST   | `/orders/trailing_stop/replace`    | relay a replace payload (`dry_run` default)|
| POST   | `/exec`                            | run an external command (shell)           |
| POST   | `/exec/mcp`                        | relay a JSON-RPC call to the Robinhood MCP |

`/exec/mcp` forwards a JSON-RPC payload to the **official Robinhood MCP**
(`https://agent.robinhood.com/mcp/trading`, HTTP transport) and relays the status
+ codes. It attaches the MCP OAuth token from `[mcp] token` (or `MCP_TOKEN_SECRET`)
— provisioned via the agentic-account OAuth flow, separate from the password
session. Body: `{"payload": <json-rpc object>, "session_id": "<optional>"}`.

```bash
curl -s localhost:8080/auth/status -H "Authorization: Bearer <exec-token>"
```

---

## Making changes (preferred workflow)

**SSH in and check first.** The VM is the source of truth for what's deployed —
before changing anything, SSH in and inspect the current state (running service,
config, what's already on disk). Don't assume; check.

Change code with minimal disruption:

1. **SSH first, check:** `systemctl status auth-service`, read the files, confirm
   what's actually running.
2. **Stage the change:** edit on the VM (or edit locally and `scp` up). Editing
   files does **not** affect the running process — the server keeps serving the
   old code until it's restarted, so changes land safely without disturbing it.
3. **Test standalone:** `./venv/bin/python -c '...'` (or run `dryrun.py` /
   `reauth_verify.py`) to exercise the new code path without touching the live
   server.
4. **Activate when ready:** `sudo systemctl restart auth-service`.

Commit to git afterward, treating the VM's files as the source of truth (pull
them down and diff before committing).

## Operations

- **Logs:** `journalctl -u auth-service -f`
- **Restart:** `sudo systemctl restart auth-service`
- **Re-auth after full token loss:** `POST /login` (device push) or
  `./venv/bin/python reauth_verify.py`
- **Config path override:** `AUTH_SERVICE_ENV=/path/to/file`; any INI key can be
  overridden by the matching env var (`GCP_PROJECT_ID`, `EXEC_TOKEN`, …).
