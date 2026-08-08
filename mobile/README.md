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

Most screens still behave like the browser version. Proactive solitude messages
are the exception: the APK has a small native bridge and an Android WorkManager
job, so receiving them does not depend on keeping the web screen open.

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

## Native proactive notifications

The APK passes the backend URL and gateway token from Settings to Android's
private app storage. WorkManager then retrieves `/api/solo/outbox`, displays each
message as a native notification, and acknowledges it at `/api/solo/outbox/ack`.

On Android 13 or newer, tap the existing notification button once to grant the
notification permission. Android's reliable periodic-work minimum is about 15
minutes, so this also works after the app is swiped away, but delivery is not
guaranteed at an exact minute. The web app does not poll or insert these messages
into chat history.

Do not commit signing keys, passwords, gateway tokens, API keys, `www/`, or
`node_modules/`.
