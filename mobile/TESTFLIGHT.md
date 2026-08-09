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
- "CDK auth" ≈ **Clerk auth**: Branchwing carries `@clerk/expo@^3.2.5` and the
  reflight api-server has `clerkProxyMiddleware.ts`. Nothing AWS-CDK exists in
  any project here. The dashboard is public-read today, so the shell ships
  without auth; when a private surface appears, port Branchwing's Clerk flow.

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
