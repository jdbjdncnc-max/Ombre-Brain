# Personal Android/Capacitor plan

## Scope

- Android only, for one person's devices.
- No iOS, app store, public release, or friend distribution work.
- Keep `companion_frontend/` as the single web-first product surface.
- Rebuild the APK only when native code, permissions, the signing identity, or
  the native bridge changes.

## Architecture

```text
companion_frontend/  -- normal web deploy --> browser users
        |
        +-- mobile web bundle -----------> downloaded, verified update
        |
        +-- mobile/www factory copy -----> offline/rollback fallback in APK

Android shell (native bridge v1)
        +-- stable settings storage
        +-- file import/export and share sheet
        +-- local notification scheduling
        +-- frontend bundle verification and rollback
```

The production shell must not point Capacitor `server.url` at the live site.
Capacitor documents that option for live reload rather than production. The
safer production design is a bundled last-known-good frontend plus validated
web-bundle updates. Capgo's open-source updater is a useful reference for the
bundle-download, `notifyAppReady`, rollback, and self-hosting pattern; adoption
will be decided separately after a license, maintenance, and privacy review.

## Delivery stages

### 1. Minimal shell (current stage)

- Pin Capacitor versions and generate only the Android project.
- Copy `companion_frontend/` into `mobile/www/` during sync.
- Keep the browser behavior unchanged and verify the shell build inputs.
- Fix the application ID as `io.github.jdbjdncncmax.ombrebrain` before the
  first signed APK.

### 2. Versioned backup and storage migration

Add a user-visible `ombre.backup.v1` JSON export/import flow before moving daily
use to the APK. It should contain messages, conversation summaries, schedules,
avatar/background references, and non-secret settings. It must not export API
keys or gateway tokens as plaintext.

Use Capacitor Preferences only for small settings and migration markers. Keep
larger history in an app-private file first; move to SQLite only when history or
search volume justifies it. Browser and Android data stores are separate, so the
first Android launch should offer an explicit import rather than pretending the
browser history moved automatically.

### 3. File import/export

Use the Android system picker and Capacitor Filesystem/Share APIs for Markdown
prompts, backup JSON, avatars, and exported conversations. Every import must be
triggered by the person using the app, validate file type/size, and show a
preview before replacing local data.

### 4. Reliable background reminders

Replace WebView polling with Capacitor Local Notifications. Schedule and cancel
native reminders whenever the schedule changes, then reconcile pending alarms
when the app resumes. Android 13+ notification permission must be requested in
context. Exact-alarm permission should be avoided unless minute-level exactness
is genuinely required; normal reminders can tolerate the operating system's
battery scheduling.

### 5. Native bridge version 1

Expose only named capabilities through official Capacitor plugins. The web app
will check a small handshake before use:

```json
{
  "bridge": "ombre.native",
  "version": 1,
  "capabilities": ["storage", "files", "notifications", "app-info"]
}
```

New web code must continue working when a capability is missing. Never expose an
unrestricted Android `addJavascriptInterface` to downloaded web content, and
only accept update bundles from the configured HTTPS origin.

### 6. Web-bundle updates and offline fallback

Publish a small manifest containing bundle version, minimum bridge version,
SHA-256, download URL, and release time. The Android shell downloads into an
app-private staging directory, verifies the hash and allowed origin, activates
the bundle only after the frontend reports ready, and retains both the previous
working bundle and the factory bundle for rollback.

This lets ordinary HTML/CSS/JavaScript and prompt/UI changes update without a
new APK. Native plugin, permission, bridge-contract, or Android project changes
still require a new APK. With no network, the newest verified local bundle (or
the factory copy) opens; server-backed chat remains unavailable but local
history and export should remain usable.

## Verification gates

Before calling the APK ready for daily use:

1. Existing Python and browser tests still pass.
2. `npm test` and `npm run cap:sync` pass from `mobile/`.
3. Android Studio builds `assembleDebug` successfully.
4. A physical Android device can install, launch, send a message, import/export
   a backup, schedule/cancel a reminder, restart offline, and roll back a broken
   web bundle.
5. The release keystore has an offline backup. Keystore files and passwords are
   never committed.

## References checked

- Capacitor installing into an existing web project:
  https://capacitorjs.com/docs/getting-started
- Capacitor Android requirements and workflow:
  https://capacitorjs.com/docs/android
- Capacitor storage guidance:
  https://capacitorjs.com/docs/guides/storage
- Capacitor Local Notifications:
  https://capacitorjs.com/docs/apis/local-notifications
- Capacitor security guidance:
  https://capacitorjs.com/docs/guides/security
- Capgo updater reference implementation:
  https://github.com/Cap-go/capacitor-updater
