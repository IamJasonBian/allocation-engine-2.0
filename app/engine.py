"""Allocation engine (reconcile runtime-desired orders against the broker).

Not implemented: the worker loop (app/background.py) no longer reconciles or
places orders — it only runs the daily stop and option take-profit sweeps. The
shape is kept so callers fail loudly instead of importing a missing module.
"""

from __future__ import annotations

DRIFT_THRESHOLD = 0.08  # 8 % — still read by app/api/drift.py


class AllocationEngine:
    def __init__(self, trader, runtime, dry_run=True, data_broker=None,
                 max_order_qty=50, risk_subject=None):
        raise NotImplementedError("AllocationEngine is not implemented")

    def register_rebalancer(self, rebalancer) -> None:
        raise NotImplementedError("AllocationEngine is not implemented")

    def tick(self):
        raise NotImplementedError("AllocationEngine is not implemented")
