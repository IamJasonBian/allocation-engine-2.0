# Trade sell-alert scheduled job (macOS launchd)

Runs `scripts/trade_sell_check.py` on a schedule and pushes a Telegram check-in
for each logged trade, flagging any that hit a sell rule.

## What it does
- Reads `data/watchlist_trades.json` (copy from `data/watchlist_trades.example.json`).
- Fetches the live underlying price (stooq, no auth).
- Evaluates each trade's `rules`: `take_profit_pct`, `trailing_stop_pct`,
  `underlying_below_breakeven`, `dte_warn`.
- Sends the result to Telegram via the bot token + chat id.

## Telegram credentials
The script resolves, in order:
1. `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from the environment, else
2. the running `claude-code-telegram` bot's `.env`
   (`~/claude-code-telegram-homely_infra_bot/.env`) for the token, and its first
   `ALLOWED_USERS` id as the chat.

## Schedule
`com.jasonzb.trade-sell-check.plist` runs **Sunday 10:00** (weekly review) and
**weekdays 15:45** local time (pre-close). Edit `StartCalendarInterval` to taste.

## Install
```bash
cp deploy/launchd/com.jasonzb.trade-sell-check.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jasonzb.trade-sell-check.plist
# run once now to verify:
launchctl start com.jasonzb.trade-sell-check
# logs:
tail -f data/trade_sell_check.log
```

## Uninstall
```bash
launchctl unload ~/Library/LaunchAgents/com.jasonzb.trade-sell-check.plist
rm ~/Library/LaunchAgents/com.jasonzb.trade-sell-check.plist
```

## Test without sending
```bash
python3 scripts/trade_sell_check.py --dry-run
```
