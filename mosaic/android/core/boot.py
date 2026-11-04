"""Entry point of the embedded MOSAIC core on Android.

Android lets an app execute native code only from its native-library
directory, where every file must be named lib*.so. CPython's extension
modules (math, _socket, _sqlite3, _ssl ...) are therefore packaged as
libpyext_<module>.so there, and this finder loads them by that name.
"""

import os
import sys
from importlib.machinery import ExtensionFileLoader
from importlib.util import spec_from_file_location

LIB_DIR = os.environ.get("MOSAIC_NATIVE_LIB_DIR", "")


class NativeLibDirFinder:
    @classmethod
    def find_spec(cls, name, path=None, target=None):
        if "." in name or not LIB_DIR:
            return None
        filename = os.path.join(LIB_DIR, f"libpyext_{name}.so")
        if not os.path.exists(filename):
            return None
        return spec_from_file_location(name, filename, loader=ExtensionFileLoader(name, filename))


sys.meta_path.append(NativeLibDirFinder)

if __name__ == "__main__":
    import runpy

    sys.argv = ["mosaic", *sys.argv[1:]]
    print(f"MOSAIC core: Python {sys.version.split()[0]} on {sys.platform}", flush=True)
    runpy.run_module("mosaic", run_name="__main__", alter_sys=True)
