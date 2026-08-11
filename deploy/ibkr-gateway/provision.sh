#!/usr/bin/env bash
# Provision the IBKR gateway VM in GCP and install Docker via startup script.
#
# The VM holds a long-lived, stateful, 2FA'd IB Gateway session — this is why
# it is a persistent GCE VM, NOT Cloud Run (stateless / scale-to-zero; see
# docs/IBKR_GATEWAY.md constraint #4).
#
# Networking: an ephemeral external IP is used for egress (pulling Docker
# images). The IBKR socket is never published — compose declares no `ports:`
# and no firewall rule opens 4001-4004, so the socket stays inside the compose
# network. Inspect it only via SSH port-forward.
#
# Credentials are NOT passed here — the no-MFA account's IB_USER / IB_PASSWORD
# live in GCP Secret Manager and are fetched on the box. Nothing secret is
# committed.
set -euo pipefail

PROJECT="${PROJECT:-allocation-agent-service}"
ZONE="${ZONE:-us-central1-a}"          # -c was out of e2-small capacity
NAME="${NAME:-ibkr-gateway-prod}"
MACHINE="${MACHINE:-e2-small}"         # 2 GB is the JVM floor

STARTUP="$(mktemp)"
cat > "$STARTUP" <<'STARTUP_SCRIPT'
#!/bin/bash
set -e
apt-get update
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
touch /var/log/docker-installed.marker
STARTUP_SCRIPT

gcloud compute instances create "$NAME" \
  --project "$PROJECT" \
  --zone "$ZONE" \
  --machine-type "$MACHINE" \
  --image-family debian-12 \
  --image-project debian-cloud \
  --boot-disk-size 20GB \
  --scopes cloud-platform \
  --shielded-secure-boot --shielded-vtpm \
  --metadata-from-file startup-script="$STARTUP"

cat <<EOF

Created $NAME in $PROJECT/$ZONE. Docker installs via the startup script
(marker: /var/log/docker-installed.marker).

Next:
  1. Create the no-MFA secondary IBKR account; store its creds in Secret Manager:
       printf '%s' 'THEUSER'  | gcloud secrets create ib-user     --data-file=- --project $PROJECT
       printf '%s' 'THEPASS'  | gcloud secrets create ib-password --data-file=- --project $PROJECT
  2. On the box (gcloud compute ssh $NAME --zone $ZONE), with this repo checked out:
       export IB_USER=\$(gcloud secrets versions access latest --secret=ib-user)
       export IB_PASSWORD=\$(gcloud secrets versions access latest --secret=ib-password)
       TRADING_MODE=live IBKR_PORT=4003 IBKR_PAPER=false DRY_RUN=true \\
         docker compose -f deploy/ibkr-gateway/docker-compose.yml up -d --build

Inspect the socket WITHOUT publishing a port:
  gcloud compute ssh $NAME --zone $ZONE -- -L 4003:localhost:4003
  python scripts/connect_probe.py --port 4003

Security invariants (see docs/IBKR_GATEWAY.md):
  - no published ports, no firewall rule for 4001-4004 (external IP is egress-only)
  - credentials via Secret Manager only, never in the repo
  - DRY_RUN=true until it survives one weekly re-auth unattended
EOF
