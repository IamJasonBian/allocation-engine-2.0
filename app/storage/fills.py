"""Trade-fill storage router — broker live reads vs local files."""

from app.config import Config


def get_trade_fills(broker) -> list[dict]:
    """Return normalized fills for PnL replay."""
    if Config.STORAGE_BACKEND == "local":
        from app.storage import local
        return local.read_trade_fills()
    return broker._fetch_live_trade_fills()


def save_trade_fills(broker) -> str:
    """Pull live fills from the broker and persist to the client store."""
    fills = broker._fetch_live_trade_fills()
    from app.storage import local
    path = local.write_trade_fills(fills)
    return str(path)
