const { STORE_NAME, json, error, options, getStore } = require("./helpers");

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") return options();

  const key = event.queryStringParameters?.key;

  try {
    const store = getStore(STORE_NAME);

    // If a specific key is requested, return that snapshot
    if (key) {
      const data = await store.get(key, { type: "json" });
      if (!data) return error(`Snapshot '${key}' not found`, 404);
      return json({ snapshot_key: key, data });
    }

    // Otherwise list all snapshot keys
    const { blobs } = await store.list();
    const keys = blobs.map((b) => b.key).sort().reverse();

    return json({
      count: keys.length,
      snapshots: keys.slice(0, 50), // Return most recent 50
    });
  } catch (e) {
    return error(`Failed to fetch snapshots: ${e.message}`);
  }
};
