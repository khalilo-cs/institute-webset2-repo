#!/usr/bin/env bash
# Builds MOSAIC.apk without Gradle, using the Android tools packaged by Ubuntu/Debian:
#   sudo apt install aapt apksigner zipalign dalvik-exchange android-sdk-platform-23 default-jdk
set -euo pipefail
cd "$(dirname "$0")"
SDK_JAR=${SDK_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}
VERSION_NAME=${VERSION_NAME:-0.5.0}
VERSION_CODE=${VERSION_CODE:-5}
KEYSTORE=${KEYSTORE:-$HOME/.mosaic-android/release.keystore}
KS_PASS=${KS_PASS:-mosaic-local}

rm -rf build && mkdir -p build/gen build/obj build/apk build/assets
echo "▸ embedded Python core"
python3 core/prepare_core.py
# optional on-device LLM engine (built by core/build_llama.sh)
for f in core/bin/libllamaserver.so core/bin/libllamaserver_dotprod.so; do
  [ -f "$f" ] && cp "$f" build/core/lib/arm64-v8a/ && echo "  + $(basename "$f")"
done
cp -r assets/. build/assets/
cp -r build/core/assets/. build/assets/
echo "▸ resources"
aapt package -f -m -J build/gen -M AndroidManifest.xml -S res -A build/assets -I "$SDK_JAR" \
  --min-sdk-version 23 --target-sdk-version 34 --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" \
  -F build/apk/unsigned.apk
echo "▸ java"
javac --release 8 -nowarn -Xlint:-options -classpath "$SDK_JAR" -d build/obj \
  src/ai/mosaic/app/MainActivity.java build/gen/ai/mosaic/app/R.java
echo "▸ dex"
dalvik-exchange --dex --min-sdk-version=23 --output=build/apk/classes.dex build/obj
(cd build/apk && aapt add -f unsigned.apk classes.dex >/dev/null)
echo "▸ native libraries (CPython, extensions, launcher)"
(cd build/core && aapt add -f ../apk/unsigned.apk lib/arm64-v8a/*.so >/dev/null)
echo "▸ align + sign"
zipalign -f -p 4 build/apk/unsigned.apk build/apk/aligned.apk
if [ ! -f "$KEYSTORE" ]; then
  mkdir -p "$(dirname "$KEYSTORE")"
  keytool -genkeypair -keystore "$KEYSTORE" -storepass "$KS_PASS" -keypass "$KS_PASS" -alias mosaic \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=MOSAIC, O=MOSAIC, C=SA" >/dev/null 2>&1
fi
apksigner sign --ks "$KEYSTORE" --ks-pass "pass:$KS_PASS" --ks-key-alias mosaic \
  --v1-signing-enabled true --v2-signing-enabled true --out build/MOSAIC.apk build/apk/aligned.apk
apksigner verify --verbose build/MOSAIC.apk | head -4
ls -la build/MOSAIC.apk
