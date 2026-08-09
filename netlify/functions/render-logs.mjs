// Render service logs, proxied for the dashboard.
//
// The dashboard is a static page; the Render API needs a bearer key, so the
// key lives here in Netlify env, never in the page:
//   RENDER_API_KEY      required
//   RENDER_OWNER_ID     required (workspace/team id, tea-…)
//   RENDER_SERVICE_IDS  optional "label:srv-id,label:srv-id" — defaults to the
//                       three services in CLAUDE.md
//
// GET /.netlify/functions/render-logs?service=<label>&limit=40

const DEFAULT_SERVICES = {
  api: "srv-d6sbe2k50q8c73fgn86g",
  worker: "srv-d6i9evua2pns73901hj0",
  feed: "srv-d6kcm3ua2pns738r97a0",
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json",
};

function services() {
  const raw = process.env.RENDER_SERVICE_IDS || "";
  if (!raw.trim()) return DEFAULT_SERVICES;
  const out = {};
  for (const pair of raw.split(",")) {
    const [label, id] = pair.split(":").map((s) => s.trim());
    if (label && id) out[label] = id;
  }
  return Object.keys(out).length ? out : DEFAULT_SERVICES;
}

function label(entry, name) {
  for (const l of entry.labels || []) if (l.name === name) return l.value;
  return null;
}

export default async (req) => {
  if (req.method === "OPTIONS") return new Response("", { headers: CORS });

  const key = process.env.RENDER_API_KEY;
  const owner = process.env.RENDER_OWNER_ID;
  if (!key || !owner) {
    return Response.json(
      { error: "RENDER_API_KEY / RENDER_OWNER_ID not configured" },
      { status: 503, headers: CORS },
    );
  }

  const svc = services();
  const url = new URL(req.url);
  const which = url.searchParams.get("service") || "worker";
  const limit = Math.min(100, Number(url.searchParams.get("limit") || 40));
  const resource = svc[which];
  if (!resource) {
    return Response.json(
      { error: `unknown service ${which}`, services: Object.keys(svc) },
      { status: 400, headers: CORS },
    );
  }

  const api = new URL("https://api.render.com/v1/logs");
  api.searchParams.set("ownerId", owner);
  api.searchParams.set("resource", resource);
  api.searchParams.set("limit", String(limit));
  api.searchParams.set("direction", "backward");

  const upstream = await fetch(api, {
    headers: { Authorization: `Bearer ${key}` },
  });
  if (!upstream.ok) {
    return Response.json(
      { error: `render api ${upstream.status}`, detail: (await upstream.text()).slice(0, 300) },
      { status: 502, headers: CORS },
    );
  }
  const body = await upstream.json();

  return Response.json(
    {
      service: which,
      resource,
      services: Object.keys(svc),
      hasMore: Boolean(body.hasMore),
      logs: (body.logs || []).map((entry) => ({
        timestamp: entry.timestamp,
        level: label(entry, "level"),
        type: label(entry, "type"),
        message: entry.message,
      })),
    },
    { headers: CORS },
  );
};
