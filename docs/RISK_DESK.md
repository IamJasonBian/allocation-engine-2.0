# Risk Desk — metrics & risk server

The Risk Desk answers six questions about the book, from the two inputs the
engine already produces (normalized fills and daily closes per symbol). Each
question is one module in `app/risk/`; `report.build_report` assembles them
and writes plain-English flags. The site at `/risk` renders the report; every
number on the page comes from `/api/risk/report` and nothing else.

| Question | Module | Key outputs |
|---|---|---|
| What did the book do each day? | `curve.py` | union-calendar mark-to-market curve: `totalPnl`, `dailyPnl`, gross/net/long/short exposure, `returnOnExposure` |
| How good was it? | `metrics.py` | annualized return & vol, Sharpe, Sortino, Calmar, max drawdown (USD, % of gross, window, duration), hit rate, profit factor, skew, kurtosis, rolling 20d vol |
| What is the book right now? | `exposure.py` | gross/net, per-symbol weights, HHI, effective N, largest position |
| How do the pieces move together? | `covariance.py` | Σ and ρ on aligned returns, true portfolio σ, marginal/component risk per symbol (hedges flagged), additive ceiling, uncorrelated floor, diversification ratio |
| How bad is a bad day? | `tail.py` | historical-simulation VaR/CVaR 95/99 on *today's* book; normal-model VaR/CVaR beside it |
| What if? | `stress.py` | uniform shocks (signed — shorts gain in a sell-off), crypto-only crash, worst observed 1-day and compounded 5-day joint moves replayed |

## Definitions

- **Growth rate** `g_t = close_t / close_{t-1} − 1`. Per-symbol σ is the sample std of `g`; downside σ is the RMS of negative `g` (Sortino denominator); latest z = `(g_T − mean) / σ`.
- **1σ move (per symbol)** `|position| × last close × σ_g` — the dollar size of a one-sigma day.
- **Return on exposure** `dailyPnl_t / grossExposure_{t-1}`. The engine has no cash ledger, so this is return on capital actually at risk, not on an account balance. Days with no prior exposure are excluded from vol/Sharpe.
- **Portfolio σ** with signed USD notionals `w`: `σ = √(wᵀΣw)`. Component risk `CR_i = w_i (Σw)_i / σ`, which sums exactly to σ; `CR_i < 0` is a hedge. Bounds: uncorrelated floor `√Σ(w_i σ_i)²`, additive ceiling `Σ|w_i| σ_i`; diversification ratio = ceiling / σ.
- **VaR / CVaR (historical)** apply every observed joint return vector to current notionals: `P&L_t = Σ_i w_i g_{i,t}`. VaR_α = −(1−α) quantile; CVaR_α = mean loss beyond it. **Parametric**: `z_α σ` and `σ φ(z_α)/(1−α)`. The gap between the two is the fat tail.
- **Annualization** 365 periods/yr when closes are near-daily (crypto), 252 on a trading-day calendar — inferred from sampling density (`len(dates) / span_days > 0.9`).

## Flags

Each flag names the number it came from so it can be checked in the section
below it. Thresholds (all on the current book):

| code | severity | fires when |
|---|---|---|
| `concentration` | warning | largest position ≥ 50% of gross |
| `drawdown` | warning | current drawdown ≥ 5% of gross |
| `tail` | critical | 1-day 99% VaR ≥ 8% of gross |
| `stress` | warning | worst hypothetical scenario ≥ 15% of gross |
| `diversification` | warning | ≥ 3 positions and diversification ratio < 1.15 |
| `zscore` | info | a held symbol printed a ≥ 2σ day |
| `skew` | info | ≥ 20 days and return skew ≤ −0.5 |
| `correlation` | info | any pair with ρ ≥ 0.8 |
| `hedge` | info | any position with negative component risk |

## API

```
GET /api/risk/report              full report (cached 60 s per broker; ?refresh=1 bypasses)
GET /api/risk/summary             headline + flags — the ops-check / Telegram payload
GET /api/risk/<section>           performance | drawdown | exposure | covariance | tail | stress | symbols | curve
GET /api/risk/symbol/<symbol>     one ticker's risk model + mark series
GET /risk                         the site
```

`?broker=` selects the broker (default `DEFAULT_BROKER`). In `APP_MODE=dev`
data comes from `data/local/trading.db`, seeded from `app/storage/samples/`
(BTC, ETH, SOL, NVDA long; TLT short; one round-tripped NVDA trade). A
sample-seeded DB is topped up with new sample symbols on boot; a DB holding a
live snapshot (`write_trade_fills`) is never mixed with samples.

## Running and deploying

```bash
APP_MODE=dev python main.py serve && open http://localhost:10000/risk
python -m pytest tests/test_risk.py tests/test_risk_api.py -q

deploy/cloudrun/deploy.sh                 # dev-mode image on Cloud Run (route-manager-prod / us-central1)
APP_MODE=prod deploy/cloudrun/deploy.sh   # live Robinhood — needs the auth-box egress path, see deploy/cloudrun/deploy.sh
```

The Cloud Run service (`risk-service`) is the same Flask app with
`ENGINE_ENABLED=false`: API + site only, no engine loop, scale-to-zero.
