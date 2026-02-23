const { json, error, options, getLatestSnapshot } = require("./helpers");

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") return options();

  try {
    const result = await getLatestSnapshot();
    if (!result) return error("No state snapshots found", 404);

    const { key, data } = result;
    const orders = data.order_book || [];
    const optionsList = data.options || [];

    return json({
      snapshot_key: key,
      timestamp: data.timestamp,
      stock_orders: orders,
      stock_order_count: orders.length,
      options: optionsList,
      options_count: optionsList.length,
    });
  } catch (e) {
    return error(`Failed to fetch orders: ${e.message}`);
  }
};
