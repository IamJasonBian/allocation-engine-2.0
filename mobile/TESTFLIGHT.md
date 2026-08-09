# Allocation Dashboard → TestFlight

WebView shell over https://allocation-engine-dashboard.netlify.app, modeled on
the Branchwing setup (`reflight/artifacts/branchwing`) — same Expo SDK 54,
same EAS profiles, same privacy-manifest block.

## What was found on this machine (and what wasn't)

- Branchwing never shipped: its `eas.json` submit block still has
  `REPLACE_WITH_*` placeholders, `eas whoami` says **Not logged in**, and no
  `AuthKey_*.p8` / fastlane / Team ID exists anywhere on disk. There are no
  reusable Apple credentials to inherit — the checklist below is the same one
  Branchwing stopped at.
- **Auth is this repo's own request-token scheme** — the same one the
  deployed stack already runs (`auth-service/gen_token.py` mints
  `secrets.token_urlsafe(32)`; the box requires `Authorization: Bearer …`;
  Render carries it as `RH_AUTH_SERVICE_REQUEST_TOKEN`). The dashboard reuses
  it: the render-logs function requires `Bearer DASHBOARD_REQUEST_TOKEN`
  (Netlify env, fail closed), the page accepts `?token=…` once and moves it to
  localStorage, and this shell injects it from `app.json → extra.dashboardToken`.
  Portfolio reads stay public Trading DB functions, matching the live site.

## One-time interactive steps (must be you — Apple 2FA)

```bash
cd mobile
npm install

npx eas-cli@latest login          # Expo account (free)
npx eas-cli@latest init           # fills app.json extra.eas.projectId
npx eas-cli@latest credentials    # registers com.optimchain.allocationdashboard
                                  # with Apple, creates signing certs (needs
                                  # Apple Developer Program, $99/yr)
```

Bundle id is `com.optimchain.allocationdashboard` — change it before the first
build if you want; it is immutable per-app after first submission.

## Build for TestFlight

```bash
# internal distribution build (installable via link on registered devices)
npx eas-cli@latest build --profile preview --platform ios

# or the store pipeline: build + upload to TestFlight
npx eas-cli@latest build --profile production --platform ios
npx eas-cli@latest submit --platform ios --latest
```

Before `submit`, fill `eas.json → submit.production.ios` with your Apple ID,
the App Store Connect app id, and the 10-char Team ID.

## Notes

- The app is intentionally a thin wrapper — the dashboard updates by
  redeploying Netlify, no app release needed. Fine for TestFlight/internal
  testing; a public App Store release would want native chrome around it
  (Apple guideline 4.2 frowns on bare web wrappers).
- Simulator smoke test without any Apple account:
  `npx eas-cli@latest build --profile development --platform ios`, or
  `npm run ios` with Xcode installed.
- `assets/` has no icon yet; Expo uses a placeholder. Add `icon.png`
  (1024×1024) before a real TestFlight round.
