"""Fetch CPython for Android (Chaquopy's build of the official CPython Android
port, from Maven Central), verify it, and lay out everything the APK needs:

  build/core/lib/arm64-v8a/    libmosaicpy.so (launcher), libpython3.13.so,
                               OpenSSL/SQLite, libpyext_<module>.so extensions
  build/core/assets/python/    python313.zip (stdlib, STORED so zipimport needs no zlib)
  build/core/assets/app/       boot.py + the mosaic package
"""

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PY = "3.13"
VERSION = "3.13.16-0"
ABI = "arm64-v8a"
BASE = f"https://repo.maven.apache.org/maven2/com/chaquo/python/target/{VERSION}"
HERE = Path(__file__).resolve().parent
ANDROID = HERE.parent
CACHE = Path.home() / ".cache" / "mosaic-android" / VERSION
OUT = ANDROID / "build" / "core"
SKIP_EXT = {"xxlimited", "xxlimited_35", "xxsubtype", "_xxtestfuzz", "_testcapi", "_testinternalcapi",
            "_testbuffer", "_testimportmultiple", "_testmultiphase", "_testsinglephase", "_testclinic",
            "_testexternalinspection", "_ctypes_test"}


def fetch(name: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    if not dest.exists():
        print(f"  downloading {name}")
        req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "mosaic-build/1.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    for algo in ("sha256", "sha1"):
        try:
            req = urllib.request.Request(f"{BASE}/{name}.{algo}", headers={"User-Agent": "mosaic-build/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                expected = r.read().decode().split()[0].strip()
        except Exception:
            continue
        actual = hashlib.new(algo, dest.read_bytes()).hexdigest()
        if actual != expected:
            dest.unlink()
            sys.exit(f"checksum mismatch for {name}")
        print(f"  verified {name} ({algo})")
        return dest
    sys.exit(f"no checksum available for {name}")


def build_launcher(lib: Path) -> None:
    tmp = ANDROID / "build" / "launcher"
    tmp.mkdir(parents=True, exist_ok=True)
    target = "--target=aarch64-linux-android23"
    subprocess.run(["clang", target, "-c", str(HERE / "launcher.S"), "-o", str(tmp / "launcher.o")], check=True)
    subprocess.run(["clang", target, "-c", str(HERE / "stub_libc.S"), "-o", str(tmp / "stub.o")], check=True)
    subprocess.run(["ld.lld", "-shared", "-soname", "libc.so", "-o", str(tmp / "libc.so"), str(tmp / "stub.o")], check=True)
    subprocess.run(["ld.lld", "-pie", "--dynamic-linker", "/system/bin/linker64", "--hash-style=both",
                    "--allow-shlib-undefined", "-z", "noexecstack", "-z", "now", "-e", "_start",
                    "-o", str(lib / "libmosaicpy.so"), str(tmp / "launcher.o"),
                    str(lib / f"libpython{PY}.so"), str(tmp / "libc.so")], check=True)


def main() -> None:
    native = fetch(f"target-{VERSION}-{ABI}.zip")
    stdlib = fetch(f"target-{VERSION}-stdlib-pyc.zip")
    if OUT.exists():
        shutil.rmtree(OUT)
    lib = OUT / "lib" / ABI
    lib.mkdir(parents=True)
    exts = 0
    with zipfile.ZipFile(native) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name.startswith(f"jniLibs/{ABI}/"):
                (lib / Path(name).name).write_bytes(z.read(info))
            elif name.startswith(f"lib-dynload/{ABI}/") and name.endswith(".so"):
                module = Path(name).name.split(".")[0]
                if module not in SKIP_EXT:
                    (lib / f"libpyext_{module}.so").write_bytes(z.read(info))
                    exts += 1
    build_launcher(lib)
    py_assets = OUT / "assets" / "python"
    py_assets.mkdir(parents=True)
    with zipfile.ZipFile(stdlib) as src, zipfile.ZipFile(py_assets / "python313.zip", "w", zipfile.ZIP_STORED) as dst:
        for info in src.infolist():
            if not info.is_dir() and not info.filename.startswith(("test/", "idlelib/", "tkinter/", "turtledemo/")):
                dst.writestr(info.filename, src.read(info), compress_type=zipfile.ZIP_STORED)
    app = OUT / "assets" / "app"
    shutil.copytree(ANDROID.parent / "mosaic", app / "mosaic",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"))
    shutil.copy(HERE / "boot.py", app / "boot.py")
    print(f"  core ready: {exts} extension modules, launcher + libpython in {lib}")


if __name__ == "__main__":
    main()
