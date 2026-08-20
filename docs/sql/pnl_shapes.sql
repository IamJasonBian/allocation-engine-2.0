-- P&L query shapes for Trading DB (public.stock_orders + public.positions)
--
-- Fill semantics: a cancelled order can still carry a partial fill, so the
-- correct fill filter is filled_quantity > 0 (not state = 'filled').
-- Order history is complete for nearly every symbol (net traded shares match
-- positions.quantity); SPY carries ~6 shares of pre-history basis, so its
-- flow P&L slightly understates true lifetime P&L.

-- ---------------------------------------------------------------------------
-- Shape 0: canonical fills CTE — start every P&L query from this
-- ---------------------------------------------------------------------------
-- WITH fills AS (
--   SELECT symbol, side, filled_quantity AS qty, average_price AS px,
--          COALESCE(updated_at, created_at) AS ts
--   FROM stock_orders
--   WHERE filled_quantity > 0 AND average_price IS NOT NULL
-- )

-- ---------------------------------------------------------------------------
-- Shape 1: total P&L per symbol (cash flow + mark-to-market)
-- sold $ - bought $ + net shares * current price. No lot matching needed;
-- exact whenever the symbol's full history is in stock_orders.
-- ---------------------------------------------------------------------------
WITH fills AS (
  SELECT symbol, CASE WHEN side = 'BUY' THEN 1 ELSE -1 END AS dir,
         filled_quantity AS qty, average_price AS px
  FROM stock_orders
  WHERE filled_quantity > 0 AND average_price IS NOT NULL
)
SELECT f.symbol,
       round(sum(CASE WHEN dir = 1  THEN qty * px END)::numeric, 2) AS bought_usd,
       round(sum(CASE WHEN dir = -1 THEN qty * px END)::numeric, 2) AS sold_usd,
       round(sum(dir * qty)::numeric, 4)                            AS net_shares,
       round(p.quantity::numeric, 4)                                AS book_shares,  -- sanity check vs net_shares
       round(p.current_price::numeric, 2)                           AS px_now,
       round((coalesce(sum(CASE WHEN dir = -1 THEN qty * px END), 0)
            - coalesce(sum(CASE WHEN dir = 1  THEN qty * px END), 0)
            + sum(dir * qty) * coalesce(p.current_price, 0))::numeric, 2) AS total_pnl
FROM fills f
LEFT JOIN positions p USING (symbol)
GROUP BY f.symbol, p.quantity, p.current_price
ORDER BY total_pnl DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- Shape 2: FIFO realized P&L per symbol
-- Buys and sells each get a cumulative-share interval [lo, hi); overlapping
-- intervals are the FIFO lot matches. Realized = matched qty * (sell - buy).
-- Sells beyond total recorded buys (pre-history basis) are left unmatched.
-- ---------------------------------------------------------------------------
WITH fills AS (
  SELECT symbol, side, filled_quantity AS qty, average_price AS px,
         COALESCE(updated_at, created_at) AS ts
  FROM stock_orders
  WHERE filled_quantity > 0 AND average_price IS NOT NULL
),
buys AS (
  SELECT symbol, px, sum(qty) OVER w - qty AS lo, sum(qty) OVER w AS hi
  FROM fills WHERE side = 'BUY'
  WINDOW w AS (PARTITION BY symbol ORDER BY ts, px ROWS UNBOUNDED PRECEDING)
),
sells AS (
  SELECT symbol, px, ts, sum(qty) OVER w - qty AS lo, sum(qty) OVER w AS hi
  FROM fills WHERE side = 'SELL'
  WINDOW w AS (PARTITION BY symbol ORDER BY ts, px ROWS UNBOUNDED PRECEDING)
)
SELECT s.symbol,
       round(sum((least(b.hi, s.hi) - greatest(b.lo, s.lo)) * (s.px - b.px))::numeric, 2) AS fifo_realized,
       round(sum( least(b.hi, s.hi) - greatest(b.lo, s.lo))::numeric, 4)                  AS matched_shares
FROM sells s
JOIN buys b ON b.symbol = s.symbol AND b.hi > s.lo AND b.lo < s.hi
GROUP BY s.symbol
ORDER BY fifo_realized DESC;

-- ---------------------------------------------------------------------------
-- Shape 3: monthly realized P&L (FIFO, attributed to the sell month)
-- Same interval match as Shape 2, grouped by when the sell happened.
-- ---------------------------------------------------------------------------
WITH fills AS (
  SELECT symbol, side, filled_quantity AS qty, average_price AS px,
         COALESCE(updated_at, created_at) AS ts
  FROM stock_orders
  WHERE filled_quantity > 0 AND average_price IS NOT NULL
),
buys AS (
  SELECT symbol, px, sum(qty) OVER w - qty AS lo, sum(qty) OVER w AS hi
  FROM fills WHERE side = 'BUY'
  WINDOW w AS (PARTITION BY symbol ORDER BY ts, px ROWS UNBOUNDED PRECEDING)
),
sells AS (
  SELECT symbol, px, ts, sum(qty) OVER w - qty AS lo, sum(qty) OVER w AS hi
  FROM fills WHERE side = 'SELL'
  WINDOW w AS (PARTITION BY symbol ORDER BY ts, px ROWS UNBOUNDED PRECEDING)
)
SELECT date_trunc('month', s.ts)::date AS month,
       round(sum((least(b.hi, s.hi) - greatest(b.lo, s.lo)) * (s.px - b.px))::numeric, 2) AS realized_pnl
FROM sells s
JOIN buys b ON b.symbol = s.symbol AND b.hi > s.lo AND b.lo < s.hi
GROUP BY 1
ORDER BY 1;

-- ---------------------------------------------------------------------------
-- Shape 4: unrealized P&L straight off the book (broker's own numbers)
-- Cross-check: Shape 1 total_pnl ≈ Shape 2 fifo_realized + this.
-- ---------------------------------------------------------------------------
SELECT symbol, quantity, avg_buy_price, current_price,
       round(profit_loss::numeric, 2)     AS unrealized_pnl,
       round(profit_loss_pct::numeric, 2) AS unrealized_pct
FROM positions
WHERE asset_type = 'equity'
ORDER BY profit_loss DESC;
