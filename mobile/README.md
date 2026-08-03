# Ombre Brain Android shell

This folder contains the personal Android-only Capacitor shell. The normal web
application remains in `../companion_frontend` and is still the canonical UI.

## What exists now

- Capacitor 8.5 is pinned to an exact version.
- `npm run web:prepare` copies the current web frontend into `www/` as the
  factory/offline fallback bundled into the APK.
- `android/` is the generated native project and should be kept in Git.
- No production `server.url` is used. A future web-bundle updater will download
  validated frontend bundles while retaining this built-in fallback.

The first shell intentionally behaves like the browser version. Native storage,
file exchange, background reminders, and live web-bundle updates are separate
follow-up stages described in `../docs/android-capacitor-plan.md`.

## Commands

Run these commands from this `mobile` folder:

```powershell
npm install
npm test
npm run cap:sync
npm run cap:open
```

To build a debug APK after Android Studio and its SDK are installed:

```powershell
npm run android:build:debug
```

The APK output will be under `android/app/build/outputs/apk/debug/`.

Do not commit signing keys, passwords, gateway tokens, API keys, `www/`, or
`node_modules/`.
