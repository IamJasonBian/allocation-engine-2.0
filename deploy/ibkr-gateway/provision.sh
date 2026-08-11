#!/usr/bin/env bash
# Provision the IBKR gateway VM in GCP. Idempotent-ish: re-running the create
# fails loudly if the instance exists (delete first, or edit in console).
#
# The VM holds a long-lived, stateful, 2FA'd IB Gateway session — this is why
# it is a persistent GCE VM, NOT Cloud Run (which is stateless / scale-to-zero;
# see docs/IBKR_GATEWAY.md constraint #4).
#
# Credentials are NOT passed here — they live in GCP Secret Manager and are
# fetched on the box into the compose environment. Nothing secret is committed.
set -euo pipefail

PROJECT="${PROJECT:-allocation-agent-service}"
ZONE="${ZONE:-us-central1-c}"
NAME="${NAME:-ibkr-gateway-prod}"
MACHINE="${MACHINE:-e2-small}"   # 2 GB is the JVM floor

gcloud compute instances create "$NAME" \
  --project "$PROJECT" \
  --zone "$ZONE" \
  --machine-type "$MACHINE" \
  --image-family debian-12 \
  --image-project debian-cloud \
  --boot-disk-size 20GB \
  --no-address \
  --shielded-secure-boot --shielded-vtpm \
  --scopes cloud-platform

cat <<EOF

Created $NAME in $PROJECT/$ZONE (no external IP).

Next (on the box, over: gcloud compute ssh $NAME --zone $ZONE):
  1. Install docker + compose plugin.
  2. Fetch IB_USER / IB_PASSWORD from Secret Manager into the shell env.
  3. From this repo checked out on the box:
       TRADING_MODE=paper IBKR_PORT=4004 IBKR_PAPER=true DRY_RUN=true \\
         docker compose -f deploy/ibkr-gateway/docker-compose.yml up -d

Inspect the socket from a laptop WITHOUT publishing a port:
  gcloud compute ssh $NAME --zone $ZONE -- -L 4004:localhost:4004
Then: python scripts/connect_probe.py --port 4004

Security invariants (see docs/IBKR_GATEWAY.md):
  - no published ports, no firewall rule for 4001-4004
  - credentials via Secret Manager only, never in the repo
  - paper + DRY_RUN=true until it survives one weekly re-auth unattended
EOF
