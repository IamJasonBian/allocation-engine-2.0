const { json, error, options, getLatestSnapshot } = require("./helpers");

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") return options();

  try {
    const result = await getLatestSnapshot();
    if (!result) return error("No state snapshots found", 404);

    const { key, data } = result;
    return json({
      snapshot_key: key,
      timestamp: data.timestamp,
      state: data.state,
      tickers: data.tickers,
      drift_metrics: data.drift_metrics || null,
    });
  } catch (e) {
    return error(`Failed to fetch state: ${e.message}`);
  }
};
