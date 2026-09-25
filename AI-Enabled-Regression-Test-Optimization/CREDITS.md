# Credits and Third-Party Notices

**AI Enabled Regression Test Optimization** — an open-source reference for
using AI to optimise regression testing and automating it with pytest,
demonstrated on an nRF52840 proof-of-concept board.

**Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.**

Original author: **Dhanya Shree S**

---

## Acknowledgement

The idea was mooted by **Mahendra Tailor**, who has been involved in
integrating Bluetooth in hearing aids.

---

## Third-party software

This project uses the following third-party components. Each is listed with
the version used, its licence, and what it is used for here. All are used
unmodified.

### Runtime dependencies — required

These are installed by `requirements.txt` and are needed for every run.

| Component | Version | Licence | Used for |
|---|---|---|---|
| [NumPy](https://numpy.org/) | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Buffer arithmetic on the audio returned by the device: RMS levels for the compression-curve test, the SNR calculation in the DSP test, and the sign-inversion count in the clipping test. |
| [pytest](https://pytest.org/) | 9.1.1 | MIT | Executing the generated test modules and emitting the JUnit XML that `reporting.py` converts into per-test logs. |
| [bleak](https://github.com/hbldh/bleak) | 3.0.2 | MIT | The Bluetooth Low Energy client used by the proof-of-concept adapter. Scanning, connecting, MTU negotiation, GATT writes and notification delivery — the whole transport between host and the nRF52840 board. |
| [pluggy](https://github.com/pytest-dev/pluggy) | 1.6.0 | MIT | pytest's plugin system. Pulled in by pytest; declared explicitly in the PyInstaller specification because it is resolved dynamically. |

### Runtime dependencies — optional

Installed only by `requirements-llm.txt`, and used only when `HA_AI=llm`.
No run imports them on the default path. One thing does: the dashboard's
Governance tab says whether each engine is ready, and answering that for
Anthropic means attempting `import anthropic` every time the tab is
redrawn. The attempt is harmless when the package is absent — the tab
reports the engine as unavailable and the run uses the rules — but it is an
import, so "never imported" would be too strong.

| Component | Version | Licence | Used for |
|---|---|---|---|
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | 0.105.2 | MIT | The client in `llm_client.py` behind both model paths: the test planner in `planner.py`, which chooses the intensity and scenarios, and the requirement-case writer in `ai_cases.py`. Both replies are constrained by a JSON schema. |

The second engine needs nothing. `gemini_client.py` reaches Google's
Generative Language API with `urllib` from the standard library, so
`HA_ENGINE=gemini` adds no package and appears nowhere in this list.

### Bundled into the packaged application

Redistributed inside `AI_Enabled_Regression_Test_Optimization.exe` and its
`_internal` directory.

| Component | Version | Licence | Used for |
|---|---|---|---|
| [Python](https://www.python.org/) | 3.12.10 | PSF License Agreement | The interpreter embedded in the executable, so no separate Python installation is required. |
| [Tcl/Tk](https://www.tcl.tk/) | 8.6.15 | Tcl/Tk License (BSD-style) | The windowing toolkit behind the Tkinter control panel: the planner switch, device picker, results table and output pane. |

The AI planner's client and its dependencies are also bundled, so the
packaged application's *With API* option works without a Python
installation. These were found by listing the executable's archive; each is
redistributed unmodified, at the version resolved when the executable was
built, under its own licence:

| Component | Licence | Pulled in by |
|---|---|---|
| anthropic | MIT | The AI planner client |
| httpx, httpcore | BSD-3-Clause | anthropic (HTTP) |
| h11 | MIT | httpcore |
| anyio, sniffio | MIT; MIT or Apache-2.0 | httpx |
| pydantic, pydantic-core, annotated-types, typing-inspection | MIT | anthropic (models) |
| jiter | MIT | anthropic (JSON) |
| cffi (`_cffi_backend.cp312-win_amd64.pyd`) | MIT | cryptography's C bindings |
| distro | Apache-2.0 | anthropic |
| docstring_parser | MIT | anthropic |
| typing_extensions | PSF-2.0 | several |
| certifi | MPL-2.0 | TLS root certificates |
| idna | BSD-3-Clause | httpx |
| google-auth, pyasn1, pyasn1-modules, requests, urllib3, charset-normalizer, cryptography | Apache-2.0; BSD-2-Clause; BSD-2-Clause; Apache-2.0; MIT; MIT; Apache-2.0 or BSD-3-Clause | Optional cloud-credential paths of anthropic |
| rich, pygments, markdown-it-py, mdurl, colorama | MIT; BSD-2-Clause; MIT; MIT; BSD-3-Clause | Console output of bundled libraries |
| iniconfig, packaging | MIT; Apache-2.0 or BSD-2-Clause | pytest |
| PyYAML, and the LibYAML statically linked into its C extension (`_internal/yaml/_yaml.cp312-win_amd64.pyd`) | MIT; MIT | Not pytest. `numpy/__config__.py` contains `_check_pyyaml`, which does `import yaml`, and PyInstaller follows that import; pygments' lexer data reaches it too. Nothing in this project imports it. |
| click | BSD-3-Clause | `httpx/_main.py`, httpx's own command line, which is bundled with httpx |
| winrt (PyWinRT), pywin32 | MIT; BSD-3-Clause | bleak's Windows backend |
| OpenSSL, libffi, zlib | Apache-2.0; MIT; zlib | Python runtime (`libcrypto-3.dll`, `libssl-3.dll`, `libffi-8.dll`, `zlib1.dll`) |
| expat 2.7.1 (`pyexpat.pyd`), libmpdec / mpdecimal 2.5.1 (`_decimal.pyd`), liblzma / XZ Utils (`_lzma.pyd`) | MIT; BSD-2-Clause; public domain or 0BSD | C libraries compiled into those three CPython 3.12.10 extension modules, which PyInstaller bundles with the standard library. Section E of the Python `LICENSE.txt` does not cover them, so expat and libmpdec have blocks of their own in `bin/THIRD_PARTY_NOTICES.txt` (texts taken from CPython's `Doc/license.rst`) and liblzma is named there by reference. The versions are those the modules report as `EXPAT_VERSION` and `__libmpdec_version__`; `_lzma.pyd` records no liblzma version, so none is claimed. |
| [OpenBLAS](https://www.openblas.net/), bundled inside NumPy 2.4.6 | BSD-3-Clause | NumPy's linear algebra (`numpy.libs/libscipy_openblas64_*.dll`). The same DLL carries **LAPACK** (BSD-3-Clause-Open-MPI; the notice is in NumPy's `LICENSE.txt`). It statically embeds **libgfortran / libgcc**, which are GPL-3.0 **with the GCC Runtime Library Exception** — the exception is what permits redistribution in a non-GPL binary, so it must not be dropped when this list is quoted. |
| Microsoft Visual C++ Runtime (`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `winrt/MSVCP140.dll`, `numpy.libs/msvcp140-*.dll`) | Microsoft redistributable licence | The C/C++ runtime the Python interpreter and the compiled extensions are built against. Redistribution is governed by the Visual Studio redistributable terms, not by an open-source licence. |

> **Keeping this list current.** It is taken from the built archive rather
> than from a lock file. After each rebuild, list what
> `bin/_internal/*.dll` and `bin/_internal/numpy.libs/` actually contain —
> the native DLLs, OpenBLAS and the Microsoft runtime included — not what
> the requirements files say.

**[PyInstaller](https://pyinstaller.org/)** 6.21.0 freezes the application
with `build_exe.spec`. It is not a dependency of the source tree, but
several parts of it are inside the executable: the Windows GUI bootloader
(its `runw.exe`), the loader modules `pyimod01_archive`,
`pyimod02_importers`, `pyimod03_ctypes` and `pyimod04_pywin32`, and the
run-time hooks `pyi_rth__tkinter`, `pyi_rth_inspect`,
`pyi_rth_multiprocessing` and `pyi_rth_pkgutil`. The bootloader and the
loader modules are GPL-2.0-or-later **with the bootloader exception** that
permits a non-GPL binary to carry them; the run-time hooks are separately
Apache-2.0. PyInstaller's `COPYING.txt` states both, and is reproduced
whole in `bin/THIRD_PARTY_NOTICES.txt`.

A fifth run-time hook in the executable, `pyi_rth_cryptography_openssl`,
is not PyInstaller's. It comes from
[pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib)
2026.6, a separate build-time package whose run-time hooks are likewise
Apache-2.0. The community hooks in that package are GPL-2.0-or-later, but
they run at build time only and no part of them enters the executable.

### Proof-of-concept target firmware

The supplied firmware image, `src/NRF_Firmware/firmware.hex`, is built for the
nRF52840 DK and links the following. Their object code is present in the
image.

| Component | Version | Licence | Used for |
|---|---|---|---|
| [Zephyr RTOS](https://zephyrproject.org/) | 4.4.0, via nRF Connect SDK v3.4.0 | Apache-2.0 | The operating system on the device: kernel, work queues, ring buffer, and the Bluetooth **host** (GATT, SMP, L2CAP). The link **controller** is Nordic's, below. |
| [nRF Connect SDK](https://developer.nordicsemi.com/) | v3.4.0 | LicenseRef-Nordic-5-Clause | Nordic's Zephyr distribution, providing the board definition and the SoftDevice controller used for the Bluetooth link. |
| [nrfx / Nordic HAL](https://github.com/NordicSemiconductor/nrfx) | bundled with NCS v3.4.0 | LicenseRef-Nordic-5-Clause | Peripheral drivers and hardware abstraction for the nRF52840. |
| SoftDevice Controller, MPSL | via nrfxlib, NCS v3.4.0 | LicenseRef-Nordic-5-Clause | The Bluetooth LE **link controller** and the protocol timing layer beneath it, linked in as closed-source binaries (`CONFIG_BT_LL_SOFTDEVICE=y`). Redistributed in `firmware.hex` / `firmware.bin`. |
| nRF Security / PSA crypto, Oberon | via nrfxlib, NCS v3.4.0 | LicenseRef-Nordic-5-Clause (Oberon Microsystems) | Cryptography behind Bluetooth pairing (`CONFIG_BT_SMP=y`, `CONFIG_NRF_SECURITY=y`, `CONFIG_PSA_CRYPTO=y`): AES, SHA-256 and ECDH P-256 from the Oberon library. |
| CryptoCell 310 driver | bundled with NCS v3.4.0 | LicenseRef-Nordic-5-Clause | Hardware-accelerated crypto on the nRF52840's CC310 (`CONFIG_DT_HAS_ARM_CRYPTOCELL_310_ENABLED=y`). |
| Mbed TLS | bundled with NCS v3.4.0 | Apache-2.0 | PSA crypto interfaces used by nRF Security. |

---

## Test material

### test_signal.wav — original

The fallback stimulus, `regression/audio/test_signal.wav`, is generated by
`tools/make_test_signal.py` and is original to this project.

The **ESC-50** environmental sound dataset is not used: it is licensed
CC BY-NC 3.0, and that non-commercial term is incompatible with releasing
this project under a permissive licence. The synthesised signal stands in
its place.

---

## Standards and specifications

| Reference | Used for |
|---|---|
| Bluetooth Core Specification, GATT and ATT | The service and characteristic layout, MTU negotiation, and the CCCD subscription the notification path depends on. |
| Bluetooth Battery Service, characteristic 0x2A19 | The battery level the proof-of-concept board reports. |
| RIFF / WAVE file format | Parsed directly in `audio_prep.py`, which handles IEEE float and `WAVE_FORMAT_EXTENSIBLE` files that Python's `wave` module rejects. |

---

## Licence

This project is released under the MIT License. See `LICENSE` for the full
text. Every Python source file carries an `SPDX-License-Identifier: MIT`
header.

Third-party components remain under their own licences as listed above.
`bin/THIRD_PARTY_NOTICES.txt` carries the licence texts that have to travel
with the packaged application.
