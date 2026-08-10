// Daily close history per symbol, for the positions-table sparklines.
//
// Proxies Yahoo Finance's chart API server-side (no key; browsers can't call
// it directly because of CORS). Public like the Trading DB reads — no token,
// nothing sensitive leaves here but market quotes.
//
// GET /.netlify/functions/price-history?symbols=MSFT,BTC.SHADOW&range=1mo
//   → { series: { MSFT: {closes:[…], change_pct: 4.2}, … }, missing: […] }

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json",
};

const CRYPTO = new Set(["BTC", "ETH", "SOL", "DOGE"]);
const MAX_SYMBOLS = 45;
const TTL_MS = 10 * 60 * 1000;

// Warm-instance cache: symbol|range → {at, data}
const cache = new Map();

function yahooSymbol(symbol) {
  // Shadow assets track the underlying: BTC.SHADOW → BTC-USD.
  const base = symbol.replace(/\.SHADOW$/i, "");
  if (CRYPTO.has(base.toUpperCase())) return `${base.toUpperCase()}-USD`;
  return base;
}

async function series(symbol, range) {
  const key = `${symbol}|${range}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.data;

  const url =
    `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol(symbol))}` +
    `?range=${encodeURIComponent(range)}&interval=1d`;
  const r = await fetch(url, { headers: { "User-Agent": "allocation-dashboard/1.0" } });
  if (!r.ok) throw new Error(`yahoo ${r.status}`);
  const body = await r.json();
  const result = body?.chart?.result?.[0];
  const closes = (result?.indicators?.quote?.[0]?.close || []).filter(
    (v) => typeof v === "number" && Number.isFinite(v),
  );
  if (closes.length < 2) throw new Error("no data");

  const data = {
    closes: closes.map((v) => Number(v.toFixed(4))),
    change_pct: Number((((closes[closes.length - 1] - closes[0]) / closes[0]) * 100).toFixed(2)),
  };
  cache.set(key, { at: Date.now(), data });
  return data;
}

export default async (req) => {
  if (req.method === "OPTIONS") return new Response("", { headers: CORS });

  const url = new URL(req.url);
  const range = ["5d", "1mo", "3mo", "6mo", "1y"].includes(url.searchParams.get("range"))
    ? url.searchParams.get("range")
    : "1mo";
  const symbols = (url.searchParams.get("symbols") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, MAX_SYMBOLS);
  if (!symbols.length) {
    return Response.json({ error: "symbols required" }, { status: 400, headers: CORS });
  }

  const out = {};
  const missing = [];
  await Promise.all(
    symbols.map(async (s) => {
      try {
        out[s] = await series(s, range);
      } catch {
        missing.push(s); // unknown ticker / delisted / yahoo miss — row just has no spark
      }
    }),
  );

  return Response.json({ range, series: out, missing }, { headers: CORS });
};
