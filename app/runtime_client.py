"""Client for the allocation-runtime-service read-only API.

Not implemented: only the removed AllocationEngine consumed it.
"""


class RuntimeClient:
    def __init__(self, base_url: str):
        raise NotImplementedError("RuntimeClient is not implemented")

    def health(self) -> dict:
        raise NotImplementedError

    def state(self) -> dict:
        raise NotImplementedError

    def orders(self) -> dict:
        raise NotImplementedError

    def portfolio(self) -> dict:
        raise NotImplementedError

    def market_data(self) -> dict:
        raise NotImplementedError

    def snapshots(self) -> dict:
        raise NotImplementedError

    def snapshot(self, key: str) -> dict:
        raise NotImplementedError
