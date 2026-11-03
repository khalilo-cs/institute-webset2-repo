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
