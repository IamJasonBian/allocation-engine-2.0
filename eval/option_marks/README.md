# Option mark eval

Batch-evaluate **allocation-engine** pricing against fixed scenarios. Pattern matches Chanto’s JSONL evals (`chanto --eval-risk …`): one case per line, a runner, a scored output.

## Pricing models (held calls)

From `app/pricing/mark_models.py` — European **Black–Scholes** marks on long calls:

| Model | Vol input | Use |
| --- | --- | --- |
| `prev_iv` | Yesterday’s IV from the option close | **Primary** mark for overnight / live spot shocks |
| `entry_iv` | First IV in the history window (default from 2026-09-29) | “Stuck at entry vol” stress |
| `realized` | 20-day trailing realized vol on the underlying | Realized-vol stress |

Helpers: `mark_call_at_spot`, `run_pricer_backtest`, `scripts/option_mark.py`, `scripts/pull_orders_and_mark.py`.

## Bounce-sleeve resolution (new long calls)

From `app/pubsub/option_pricer.py` — given a **chain snapshot** + **recipe** (see `app/pubsub/bounce.py`: 21–35 DTE, ~0.22Δ call, day limit). Produces strike, expiry, and bid-side limit; quotes never go on the bus.

## Run locally

```bash
cd allocation-engine-2.0
./eval/option_marks/run_all.sh
```

Or step-by-step:

```bash
PYTHONPATH=. python3 eval/option_marks/run_eval.py
PYTHONPATH=. python3 eval/option_marks/run_eval.py --live   # NBIS spot from Trading DB
PYTHONPATH=. python3 scripts/pull_orders_and_mark.py
```

Outputs `eval/option_marks/scored.jsonl` and a short summary on stderr.

## Run within Chanto

**Local background run** (includes uncommitted work; no push):

```bash
cd allocation-engine-2.0
chanto run --bg "bash eval/option_marks/run_all.sh; echo exit=\$?"
chanto attach <run-id>
chanto runs show <run-id>
```

**Chanto cloud** ([app.chanto.sh](https://app.chanto.sh/app/sessions)) requires:

1. `chanto cloud login` (done on this machine as **jason.bian75**).
2. A **self-hosted runner** (`hosted` is off on the free org): [Runners](https://app.chanto.sh) → create token → `chanto runner connect https://api.chanto.sh --registration-token hrr_…`
3. **Commit + push** the option-mark stack (`eval/`, `app/pricing/`, related tests/scripts) — cloud clones `origin`, not your dirty tree.

Then:

```bash
chanto run --cloud --push "bash eval/option_marks/run_all.sh; summarize NBIS marks and GOOGL bounce tickets."
```

Task template for iterative eval work:

> Refresh `fixtures/googl_chain.json`, run `./eval/option_marks/run_all.sh`, tighten `cases.jsonl` bands if live marks drift.

## Case kinds

- `held_mark` — symbol, strike, expiry, `spot`, `as_of`; optional `expect_marks` with ± tolerance.
- `resolve_long_call` — `chain` fixture path + bounce `recipe`; optional expected strike/limit band.
