# Option alerts (worker)

Telegram 🐧 messages come from **declared rulesets** in
`app/risk/option_alert_rulesets/`. Pattern matches our
validation_service style: rulesets are listed in code,
enabled by name via env, each documents when it fires and owns its dedupe state.

## Enabled rulesets

| Env | Default |
|-----|---------|
| `OPTION_ALERT_RULESETS_ENABLED` | `drawdown,mental_stop` |

Unknown codes fail at worker startup (`build_registry`).

## Rulesets

| Code | When it fires | Dedupe |
|------|----------------|--------|
| `drawdown` | Long leg only; mark **more than 25%** below `purchase_price` | Once per breach episode; re-arms when mark recovers |
| `mental_stop` | Net long or vertical spread; loss vs cost crosses **40% / 60% / 80%**; until `OPTION_MENTAL_STOPS_UNTIL` | Once per tier; re-arm after **10pt** recovery below tier |

Both run every worker poll (`POLL_INTERVAL_SECONDS`, typically 300 on Render).

## Message shape

```
🐧 NBIS CALL 230.0 2026-11-20 x1
Buy $2.08 → Now $0.95 (-54%, $-113)
```

No trade is placed; alerts are informational only.

## Requires

- `ALERTS_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` on the **worker**
- Correct `mark_price` from Robinhood (see PR #160 nested market-data fix)
