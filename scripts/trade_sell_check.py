#!/usr/bin/env python3
"""Scheduled sell-alert check for logged option trades -> Telegram.

Reads a logged-trades file, refreshes the underlying price (no-auth, via stooq),
evaluates each trade's sell rules, and sends a Telegram check-in to the
configured chat. Designed to run unattended from launchd/cron.

Telegram delivery reuses the same bot the rest of the stack uses:
  - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment, OR
  - falls back to the running claude-code-telegram bot's .env + ALLOWED_USERS.

Sell rules (per trade, in the JSON `rules` block):
  take_profit_pct          alert when total return >= this %
  trailing_stop_pct        alert when option value falls this % from its peak
  underlying_below_breakeven  alert when the underlying trades below breakeven
  dte_warn                 alert when days-to-expiration <= this
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADES_FILE = Path(os.getenv("TRADES_FILE", REPO_ROOT / "data" / "watchlist_trades.json"))
BOT_ENV_FALLBACK = Path.home() / "claude-code-telegram-homely_infra_bot" / ".env"
DEFAULT_CHAT_ID = "5921617034"


# -- config -----------------------------------------------------------------

def _read_env_file(path: Path, key: str) -> str:
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _telegram_creds() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or _read_env_file(BOT_ENV_FALLBACK, "TELEGRAM_BOT_TOKEN")
    chat = (
        os.getenv("TELEGRAM_CHAT_ID")
        or _read_env_file(BOT_ENV_FALLBACK, "ALLOWED_USERS").split(",")[0].strip()
        or DEFAULT_CHAT_ID
    )
    return token, chat


# -- prices -----------------------------------------------------------------

def fetch_underlying_price(symbol: str) -> float | None:
    """Last price for a US ticker via stooq CSV (no auth)."""
    url = f"https://stooq.com/q/l/?s={symbol.lower()}.us&f=sd2t2ohlcvn&h&e=csv"
    try:
        with urlopen(url, timeout=10) as resp:
            rows = list(csv.DictReader(io.StringIO(resp.read().decode())))
        if rows:
            close = rows[0].get("Close")
            if close and close not in ("N/D", "N/A"):
                return float(close)
    except Exception:
        pass
    return None


# -- evaluation -------------------------------------------------------------

def evaluate(trade: dict, underlying_price: float | None) -> tuple[list[str], dict]:
    """Return (sell_signals, context) for one trade."""
    rules = trade.get("rules", {})
    cost = float(trade.get("cost_per_contract", 0))
    value = trade.get("current_value")
    value = float(value) if value is not None else None
    peak = float(trade.get("peak_value") or value or 0)
    breakeven = float(trade.get("breakeven", 0))

    exp = datetime.strptime(trade["expiration"], "%Y-%m-%d").date()
    dte = (exp - date.today()).days

    signals: list[str] = []

    if value is not None and cost > 0:
        ret_pct = (value - cost) / cost * 100
        tp = rules.get("take_profit_pct")
        if tp is not None and ret_pct >= float(tp):
            signals.append(f"🎯 TAKE PROFIT: +{ret_pct:.0f}% ≥ target +{float(tp):.0f}%")
        trail = rules.get("trailing_stop_pct")
        if trail is not None and peak > 0 and value <= peak * (1 - float(trail) / 100):
            drop = (peak - value) / peak * 100
            signals.append(f"📉 TRAILING STOP: down {drop:.0f}% from peak ${peak:.2f} (trail {trail}%)")
    else:
        ret_pct = None

    if rules.get("underlying_below_breakeven") and underlying_price is not None and breakeven:
        if underlying_price < breakeven:
            signals.append(f"⚠️ BELOW BREAKEVEN: {trade['underlying']} ${underlying_price:.2f} < ${breakeven:.2f}")

    dte_warn = rules.get("dte_warn")
    if dte_warn is not None and dte <= int(dte_warn):
        signals.append(f"⏳ TIME STOP: {dte} day(s) to expiration ≤ {dte_warn}")

    return signals, {"dte": dte, "ret_pct": ret_pct, "value": value, "cost": cost}


def format_message(trade: dict, underlying_price: float | None, signals: list[str], ctx: dict) -> str:
    sym = trade["underlying"]
    label = f"{sym} ${trade['strike']:g}{trade['option_type'][0].upper()} (exp {trade['expiration']})"
    up = f"${underlying_price:.2f}" if underlying_price is not None else "n/a"
    be = trade.get("breakeven")
    lines = [f"📊 Trade check-in — {label}"]
    lines.append(f"{sym}: {up}" + (f"  ·  breakeven ${be:.2f}" if be else ""))
    if ctx["value"] is not None:
        ret = f" ({ctx['ret_pct']:+.0f}%)" if ctx["ret_pct"] is not None else ""
        lines.append(f"Option: ${ctx['value']:.2f} (added ${ctx['cost']:.2f}){ret}  ·  as of last update")
    lines.append(f"DTE: {ctx['dte']}")
    lines.append("")
    if signals:
        lines.append("Signals:")
        lines.extend(f"  {s}" for s in signals)
    else:
        lines.append("✅ Holding — no sell triggers")
    return "\n".join(lines)


# -- telegram ---------------------------------------------------------------

def send_telegram(text: str) -> bool:
    import requests  # local import so --dry-run works without the dep installed
    token, chat = _telegram_creds()
    if not token or not chat:
        print("[telegram] missing TELEGRAM_BOT_TOKEN / chat id — not sending", file=sys.stderr)
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )
    ok = resp.ok and resp.json().get("ok", False)
    print(f"[telegram] sent={ok} status={resp.status_code}")
    return ok


# -- main -------------------------------------------------------------------

def main() -> int:
    dry_run = "--dry-run" in sys.argv
    data = json.loads(TRADES_FILE.read_text())
    trades = data.get("trades", [])
    if not trades:
        print("No trades logged.")
        return 0

    blocks: list[str] = []
    for trade in trades:
        underlying = fetch_underlying_price(trade["underlying"])
        signals, ctx = evaluate(trade, underlying)
        blocks.append(format_message(trade, underlying, signals, ctx))

    message = "\n\n".join(blocks)
    print(message)
    if dry_run:
        print("\n[dry-run] not sending to Telegram")
        return 0
    return 0 if send_telegram(message) else 1


if __name__ == "__main__":
    raise SystemExit(main())
