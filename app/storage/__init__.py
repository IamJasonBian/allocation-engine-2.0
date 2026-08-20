"""Storage layer facade — one import surface over everything the app persists.

Wraps the existing store modules (left in place; legacy import paths still
work) plus the local fill store introduced for dev mode:

    Store                 Medium                          Used by
    -----                 ------                          -------
    redis                 Redis hashes (stocks/orders)    engine sync (background.py)
    s3                    S3 order events + snapshots     engine sync, /api/events
    trading_db            Postgres via Netlify functions  engine sync -> dashboard
    option_history        Netlify Blobs history           engine sync (options)
    local                 data dir JSON (dev fills)       PnL replay in dev mode

Fill routing (`get_trade_fills` / `save_trade_fills`) picks between live
broker reads and the local store based on the app mode in configs/config.json.
"""

from app import option_history_store as option_history
from app import redis_store as redis
from app import s3_store as s3
from app import trading_db
from app.storage import local
from app.storage.fills import get_trade_fills, save_trade_fills

__all__ = [
    "redis",
    "s3",
    "trading_db",
    "option_history",
    "local",
    "get_trade_fills",
    "save_trade_fills",
]
