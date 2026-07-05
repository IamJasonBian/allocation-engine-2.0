"""GCP Secret Manager access over the REST API.

We avoid the google-cloud-secret-manager client library on purpose: it pulls in
grpc + protobuf (100 MB+ resident), which is wasteful on an e2-micro. Instead we
grab an OAuth token from the GCE metadata server and call the Secret Manager
REST endpoint directly with `requests`.

Locally (off-GCE), set GOOGLE_ACCESS_TOKEN (e.g. `gcloud auth print-access-token`)
and the metadata path is skipped.
"""

import base64
import os

import requests

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_METADATA_PROJECT_URL = (
    "http://metadata.google.internal/computeMetadata/v1/project/project-id"
)
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}


def _access_token() -> str:
    """OAuth access token: env override first, else the metadata server."""
    override = os.getenv("GOOGLE_ACCESS_TOKEN")
    if override:
        return override
    resp = requests.get(_METADATA_TOKEN_URL, headers=_METADATA_HEADERS, timeout=5)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _project_id(explicit: str = "") -> str:
    """Project id from config, else discovered from the metadata server."""
    if explicit:
        return explicit
    resp = requests.get(_METADATA_PROJECT_URL, headers=_METADATA_HEADERS, timeout=5)
    resp.raise_for_status()
    return resp.text


def get_secret(name: str, project_id: str = "", version: str = "latest") -> str:
    """Return the decoded payload of a Secret Manager secret.

    `name` may be a bare secret id ("auth-service-password") or a full resource
    name ("projects/123/secrets/foo/versions/latest").
    """
    if name.startswith("projects/"):
        resource = name if "/versions/" in name else f"{name}/versions/{version}"
    else:
        project = _project_id(project_id)
        resource = f"projects/{project}/secrets/{name}/versions/{version}"

    url = f"https://secretmanager.googleapis.com/v1/{resource}:access"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {_access_token()}"},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()["payload"]["data"]
    return base64.b64decode(payload).decode("utf-8")
