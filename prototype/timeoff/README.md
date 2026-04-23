# Time Off — prototype

Static, single-page prototype for a vacation planner + boss-conversation coach.

- `index.html` — UI shell (Tailwind via CDN)
- `app.js` — planner scoring, draft generator, rehearsal chat, localStorage profile/history

No backend, no build step. Open `index.html` in a browser, or serve the folder:

```
python3 -m http.server -d prototype/timeoff 8765
```

Then visit http://localhost:8765.

## What works
- **Planner**: scores 3 trip windows using PTO burn, synthetic flight-price model, US holiday bridges, team coverage, and workload.
- **Coach**: drafts Slack / email / 1:1 talking points from the picked window. Rehearsal has three boss personas (skeptic, warm, blunt) with 3-turn scripted responses.
- **Debrief**: flags which of the four bosses-always-probe topics (coverage, risk, flex, offline) you hit.
- **Profile + history**: persisted in `localStorage`.

## Intentionally faked
- Flight prices are a heuristic, not a live API.
- Rehearsal uses canned turn templates, not an LLM. Swap in an API call to upgrade.
- No calendar, Slack, or HRIS integrations yet.
