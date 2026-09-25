# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# PyInstaller spec for the regression dashboard.
#
#   pyinstaller build_exe.spec --noconfirm
#
# Produces dist/AI_Enabled_Regression_Test_Optimization/AI_Enabled_Regression_Test_Optimization.exe
#
# Two things make this app awkward to freeze, and both are handled here:
#
#   1. It generates test modules and runs pytest on them. pytest discovers
#      plugins through entry points, which PyInstaller cannot see, so every
#      plugin and internal module it needs is listed in hiddenimports below.
#
#   2. A frozen exe has no interpreter to re-launch. The dashboard therefore
#      re-invokes itself with --run-pipeline instead of calling
#      "python -m regression.ai_engine.orchestrator"; see regression/dashboard.py.
#
# onedir, not onefile: onefile unpacks to a temp directory on every launch,
# which makes startup slow and makes the writable paths beside the exe
# confusing. onedir also keeps the writable regression/ folder next to the
# exe, where logs and generated suites are easy to find. Read-only assets
# are a separate matter: paths.asset resolves those inside
# _internal/regression/, not beside the exe.

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "pytest",
    "py",
    "_pytest",
    "pluggy",
    "bleak",
    "numpy",
]

# Modules imported only by the GENERATED test modules (test_ai_generated.py
# and test_from_canonical.py). PyInstaller analyses the source tree, and
# neither file exists at analysis time, so nothing here would otherwise be
# bundled. Keep in sync with the templates in
# regression/ai_engine/generate_tests.py and
# regression/ai_engine/generate_from_canonical.py -- the list carried audioop,
# unittest and unittest.mock, which no generated module imports; audioop is
# gone from the standard library in 3.13, so bundling it was a future
# build failure for no gain.
hiddenimports += [
    "wave",
    "asyncio",
]

# The analysis layer: reached only from the CLI and from the release-note
# path, never from an import the dashboard makes at start-up, so PyInstaller
# does not see them. Without these the packaged build can run a pipeline but
# cannot read a release note into a change specification.
hiddenimports += [
    "regression.ai_engine.change_spec",
    "regression.ai_engine.test_spec",
    "regression.ai_engine.requirement_tests",
    "regression.ai_engine.canonical",
    "regression.ai_engine.generate_from_canonical",
    "regression.change_detection.firmware_facts",
    # Imported only by the generated requirement suite.
    "regression.measurements",
]

# pytest resolves most of its own machinery dynamically.
hiddenimports += collect_submodules("_pytest")
hiddenimports += collect_submodules("pluggy")

# The bleak backend is selected at runtime by platform.
hiddenimports += collect_submodules("bleak.backends")

datas = [
    # Test stimulus, read by the generated tests.
    ("regression/audio/test_signal.wav", "regression/audio"),

    # The ble_device fixture. pytest resolves conftest.py from the filesystem,
    # not from the bundle, and in a frozen build there is no pytest.ini, so
    # rootdir collapses to the directory holding the generated module and only
    # a conftest.py sitting there is loaded. Without this file every hardware
    # test errors with "fixture 'ble_device' not found".
    ("regression/generated_tests/conftest.py", "regression/generated_tests"),
]

# Fingerprint of the firmware this release was built against, written by
#
#   python -m regression.change_detection.firmware_diff --save-baseline
#
# A packaged build has no NRF_Firmware tree and no run history, so the first
# firmware comparison has nothing on the other side without it. Optional on
# purpose: freezing must not require an ARM toolchain on the build machine,
# and the app reports the baseline as absent rather than inventing one.
import os as _os

_baseline = "regression/artifacts/firmware/baseline.json"

if _os.path.exists(_baseline):
    datas.append((_baseline, "regression/artifacts/firmware"))
else:
    print("build_exe.spec: no firmware baseline bundled ({} absent)".format(
        _baseline))


a = Analysis(
    ["regression/dashboard.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # "dotenv" is here because the key is typed into the window and held in
    # that process only -- there is no .env file to read, and no module
    # imports the package. PyInstaller was bundling it anyway (something on
    # the dependency graph imports it optionally), which meant the binary
    # shipped code the project does not use and a licence notice for it.
    # setuptools is excluded for the same reason as dotenv: nothing in this
    # project imports it, PyInstaller was bundling it anyway, and it drags in
    # a tree of vendored MIT packages (jaraco.*, more_itertools, tomli,
    # backports.tarfile, packaging) whose notices would all have to travel
    # with the binary. Excluding it also drops the one bundled path with a
    # space in it, setuptools/_vendor/jaraco/text/Lorem ipsum.txt.
    excludes=["matplotlib", "scipy", "PIL", "PyQt5", "PySide2", "dotenv",
              "setuptools", "pkg_resources"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI_Enabled_Regression_Test_Optimization",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI_Enabled_Regression_Test_Optimization",
)
