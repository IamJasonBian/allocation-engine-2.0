## Order Price Validation (TODO)

- Currently the $50 max order limit uses the limit_price from the runtime service without independent validation
- Future: Validate the price via Alpaca market data before submitting orders
- Weekend handling: When markets are closed (weekends/holidays), use a BTC/USD proxy to adjust and track price movements so the engine has a reasonable price reference when markets reopen
