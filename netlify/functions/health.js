const { json, options } = require("./helpers");

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") return options();

  return json({
    status: "ok",
    service: "allocation-engine-api",
    version: "0.1.0",
    timestamp: new Date().toISOString(),
  });
};
