# MOSAIC Android app

This Android application packages the existing MOSAIC Python engine. It does **not**
package or display the institute website.

## Architecture

- Native Android launcher activity and WebView shell.
- Python 3.13 runtime embedded with Chaquopy.
- The existing `mosaic` Python package and its static Arabic interface are loaded
  from the parent MOSAIC project directory; the engine is not copied or forked.
- The API binds to an ephemeral `127.0.0.1` port only. It is not exposed to the LAN.
- SQLite data is stored in the app's private files directory as `mosaic.db`.
- Battery, charging, metered-network and thermal state are sent to the existing
  resource scheduler when the app is opened or resumed.

The installed app requires a 64-bit ARM Android device (`arm64-v8a`) or an
x86_64 emulator. Minimum Android API level: 24.

## Build locally

Install JDK 17, Android SDK Platform 36 and Android Build Tools 36.0.0, then run:

```bash
cd mosaic/android
gradle assembleDebug
```

The APK is produced at:

```
app/build/outputs/apk/debug/app-debug.apk
```

The GitHub Actions workflow at the repository root builds this project whenever
files under `mosaic/` change and uploads the debug APK as the
`mosaic-debug-apk` workflow artifact.

## Data and current engine scope

MOSAIC keeps its SQLite database in Android's private app storage. Uninstalling
the app removes that local database. The app starts with an empty MOSAIC database;
create a category and add verified examples in the **تعليم** tab.

The embedded engine is the current research prototype described in
`../README.md`. It is a continual-learning engine with verified skill and
knowledge categories, not yet a general-purpose conversational language model.
External teacher integrations are not configured by this Android build.


## MOSAIC Android extended workspaces

The Android shell now exposes:
- Native Android speech recognition (microphone permission is requested only when you tap the microphone) and Arabic text-to-speech.
- Android Storage Access Framework file picker. MOSAIC reads only files the user explicitly selects; the current reader supports text, Markdown, CSV, JSON and log files up to 1 MB each.
- A website research workspace. It stores public HTML text in a separate local SQLite knowledge index and shows source URLs, timestamps and content hashes. Up to 8 same-origin pages are fetched per request.
- Retrieval of saved website excerpts for non-private questions, with source links surfaced in the UI. Private questions do not attach web excerpts or call external teachers through this feature.

Website research is intentionally bounded: it honours robots.txt, blocks local/private network addresses and unsafe redirects, and does not bypass authentication, paywalls, CAPTCHAs, or access controls. A page being indexed does not mean its claims are verified.

Image and video workspaces are currently interface placeholders. Real generation still requires choosing and configuring a compatible generation provider and securely provisioning its API credentials. Do not put provider secrets in the Android APK or source repository.
