"""IBKRTrader — BrokerClient implementation backed by the IBKR Client Portal Web API.

Composes the session lifecycle (session.py), contract resolution (contracts.py),
order placement (orders.py) and portfolio reads (positions.py). Every public
method establishes/refreshes the brokerage session via ``ensure_auth`` first,
mirroring ``RobinhoodTrader._ensure_auth``.
"""

import logging

from app.brokers.base import BrokerClient
from app.brokers.ibkr import contracts, orders, positions
from app.brokers.ibkr.session import IBKRSession

log = logging.getLogger(__name__)


class IBKRTrader(BrokerClient):
    def __init__(
        self,
        account_id: str,
        *,
        paper: bool = True,
        consumer_key: str = "",
        access_token: str = "",
        access_token_secret: str = "",
        dh_prime: str = "",
        signature_key_path: str = "",
        encryption_key_path: str = "",
    ):
        self.account_id = account_id
        self.paper = paper
        self._session = IBKRSession(
            account_id,
            paper=paper,
            consumer_key=consumer_key,
            access_token=access_token,
            access_token_secret=access_token_secret,
            dh_prime=dh_prime,
            signature_key_path=signature_key_path,
            encryption_key_path=encryption_key_path,
        )

    @property
    def _client(self):
        return self._session.client

    # -- account / positions ------------------------------------------------

    def account(self) -> dict:
        self._session.ensure_auth()
        return positions.account(self._client, self.account_id)

    def positions(self) -> list[dict]:
        self._session.ensure_auth()
        return positions.positions(self._client, self.account_id)

    def options_positions(self) -> list[dict]:
        self._session.ensure_auth()
        return positions.options_positions(self._client, self.account_id)

    # -- orders -------------------------------------------------------------

    def open_orders(self) -> list[dict]:
        self._session.ensure_auth()
        return orders.open_orders(self._client, self.account_id)

    def options_orders(self, limit: int = 50, open_only: bool = False) -> list[dict]:
        self._session.ensure_auth()
        return orders.options_orders(self._client, limit=limit, open_only=open_only)

    def submit_order(self, order: dict) -> dict | None:
        self._session.ensure_auth()
        return orders.submit_order(self._client, self.account_id, order)

    def submit_option_order(self, order: dict) -> dict | None:
        """Resolve the option contract then place the order with reply/confirm handling."""
        self._session.ensure_auth()
        try:
            conid = contracts.resolve_option_conid(
                self._client,
                order["chain_symbol"],
                order["expiration"],
                float(order["strike"]),
                order["option_type"],
            )
        except Exception as e:
            log.error("[ibkr] Failed to resolve option conid: %s", e)
            return None

        if conid is None:
            log.error("[ibkr] Could not resolve conid for %s %s %s %s",
                      order.get("chain_symbol"), order.get("expiration"),
                      order.get("strike"), order.get("option_type"))
            return None

        return orders.submit_option_order(self._client, self.account_id, conid, order)

    def cancel_order(self, order_id: str) -> None:
        self._session.ensure_auth()
        orders.cancel_order(self._client, self.account_id, order_id)

    def cancel_all(self) -> None:
        self._session.ensure_auth()
        orders.cancel_all(self._client, self.account_id)

    # -- auth status --------------------------------------------------------

    def auth_status(self) -> dict:
        return self._session.auth_status()
