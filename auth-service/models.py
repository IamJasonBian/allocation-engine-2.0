"""Immutable value objects for the auth-service.

Everything the login flow produces is a frozen dataclass — credentials pulled
from Secret Manager and the resulting Robinhood session are both immutable once
constructed, so nothing downstream can mutate a token or password in place.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Credentials:
    """Secrets fetched from GCP Secret Manager (never logged)."""

    username: str
    password: str
    totp_secret: str | None = None   # base32; optional
    device_token: str | None = None  # stable per-device UUID

    def __repr__(self) -> str:  # keep secrets out of logs/tracebacks
        return f"Credentials(username={self.username!r}, password=***, totp=***)"


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated Robinhood session."""

    access_token: str
    refresh_token: str
    token_type: str          # "Bearer"
    expires_at: float        # epoch seconds
    device_token: str
    account_url: str = ""
    account_number: str = ""

    def headers(self) -> dict:
        """Headers to pass to https://api.robinhood.com/."""
        return {
            "Authorization": f"{self.token_type} {self.access_token}",
            "Accept": "application/json",
        }

    def with_account(self, account_url: str, account_number: str) -> "Session":
        return replace(self, account_url=account_url, account_number=account_number)

    def __repr__(self) -> str:
        return (
            f"Session(token_type={self.token_type!r}, access_token=***, "
            f"expires_at={self.expires_at}, account_number={self.account_number!r})"
        )


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Outcome of an auth attempt — relayed to callers for alerting.

    status is one of: OK, NEEDS_APPROVAL, MFA_REQUIRED, TIMEOUT, ERROR.
    error_code is a stable machine string; detail is human-readable.
    """

    status: str
    session: Session | None = None
    approval_id: str | None = None
    error_code: str | None = None
    detail: str = ""
