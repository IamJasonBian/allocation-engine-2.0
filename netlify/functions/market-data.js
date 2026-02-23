const { json, error, options, getLatestSnapshot } = require("./helpers");

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") return options();

  try {
    const result = await getLatestSnapshot();
    if (!result) return error("No state snapshots found", 404);

    const { key, data } = result;

    // Extract ticker metrics from state
    const metrics = {};
    if (data.tickers) {
      for (const [symbol, ticker] of Object.entries(data.tickers)) {
        metrics[symbol] = {
          order_count: (ticker.orders || []).length,
          signal_order_count: (ticker.signal_orders || []).length,
        };
      }
    }

    return json({
      snapshot_key: key,
      timestamp: data.timestamp,
      ticker_metrics: metrics,
      drift_metrics: data.drift_metrics || null,
    });
  } catch (e) {
    return error(`Failed to fetch market data: ${e.message}`);
  }
};
