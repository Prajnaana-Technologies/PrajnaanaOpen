# RPC Framework — Android Guide (CORE1)

This document describes how CORE1 is realized as two Android applications, how they
are built from the C framework, and how the phone is operated as an RPC client or
TCP server. The function set and cores are documented in **USER_GUIDE.md**; the internal
mechanisms are documented in **TECHNICAL_GUIDE.md**.

---

## 1. Application Components

CORE1 is not a console program; it is realized as two applications that share the
package `com.example.aidlserver`:

| Application | Role |
|-------------|------|
| **aidlserver** | A headless `Service` (`rpcserverservice`, with no launcher icon). It loads the C framework as `librpctest.so` (built from `jni/` via the NDK) and exposes it over an **AIDL** binder interface, `IRpc`. This process opens the TCP sockets. |
| **rpcclient2** | The user interface. It binds the `aidlserver` service and drives it (TCP server IP, function selection, result display). It contains no native code. |

A single call proceeds as follows: `rpcclient2` (UI) → `IRpc` binder →
`rpcserverservice` (Java) → JNI wrapper in `jni/hello.c` → generated `fn_*` client
stub → RPC over TCP.

> **Both applications must be installed.** rpcclient2 starts aidlserver
> automatically via `BIND_AUTO_CREATE`. If only rpcclient2 is installed, the bind
> fails and the UI reports *"Service not bound yet"*.

---

## 2. Development Environment

The environment is kept separate from any AOSP or vendor tree:

- Android Studio and the SDK reside under `~/Android` (`~/Android/Sdk`), with the NDK
  at `~/Android/Sdk/ndk/<version>`.
- **The Gradle JDK must be JDK 17** (`~/Android/jdk17`); a newer JBR fails to compile
  the Gradle scripts (`Unsupported class file major version`). In Android Studio:
  Settings → Build Tools → Gradle → Gradle JDK → jbr-17 / `~/Android/jdk17`.
- Command-line builds use `JAVA_HOME=~/Android/jdk17 ./gradlew …`.

The two projects reside in `~/AndroidStudioProjects/aidlserver` and `…/rpcclient2`;
mirror copies (the source of truth for the C components) reside under
`~/Documents/RPC_ReferenceFramework/`.

---

## 3. Server Project (aidlserver)

This project follows the standard setup sequence: empty project → native → AIDL →
service → manifest.

### 3.1 Native C in `jni/`
Copy the CORE1 C files into the `jni/` folder adjacent to `app`. In the repository
these sources live in `src/rpc_src/` and `inc/` (the generator writes them there);
they are flattened into `jni/` for the NDK build:
```
jni/  hello.c  main_file.c  osal.c  os_platform.h
      rpc_marshall.c  rpc_marshall.h  rpc_fn.h  rpc_fncode.h
      rpc_core1.c  rpc_core1_client.c  rpc_core1_server.c
      Android.mk  Application.mk
```
- `hello.c` is the **JNI bridge** (the
  `Java_com_example_aidlserver_rpcserverservice_*` entry points).
- The `rpc_fn.h` used here contains one Android-specific adjustment: `printf` is
  redirected to logcat (`__android_log_print`, tag `RPC_MAIN`).
- The generated `rpc_core1_*.c` (in `src/rpc_src/`) and `rpc_fncode.h` (in `inc/`)
  are produced directly by `python src/python_scripts/generator_code.py` (see §6).

### 3.2 Compiling the native library
Two equivalent methods are available:
- **Manual** (produces `libs/<abi>/librpctest.so`):
  ```
  export ANDROID_SDK_ROOT=~/Android/Sdk
  ndk-build NDK_PROJECT_PATH=. NDK_APPLICATION_MK=jni/Application.mk APP_ABI=all
  ```
- **Gradle-integrated** (recommended, and used by this project) — in
  `app/build.gradle`:
  ```gradle
  externalNativeBuild { ndkBuild { path file('../jni/Android.mk') } }
  sourceSets { main { jniLibs.srcDirs = [] } }   // avoid packaging stale libs/*.so
  ```
  The `.so` is then rebuilt automatically on every application build, with no manual
  step and no stale library.

### 3.3 AIDL
- Place `IRpc.aidl` and `RpcData.aidl` under
  `app/src/main/aidl/com/example/aidlserver/`.
- Enable AIDL: in `gradle.properties`, set
  `android.defaults.buildfeatures.aidl=true` (and/or `buildFeatures { aidl true }`),
  and set `sourceSets { main { aidl.srcDirs = ['src/main/aidl'] } }` in
  `build.gradle`.
- Sync and build; the AIDL stub (`IRpc.Stub`) is generated.

### 3.4 Service and binder
- `rpcserverservice extends Service`, located in `src/main/java/…/aidlserver/`.
- `static { System.loadLibrary("rpctest"); }` and `public native …` declarations for
  each JNI entry point (`startrpc`, `setServerIp`, `setServerCore`, `getLocalIp`,
  `corePresent`, `computesqr`, and others).
- The binder `IRpc.Stub` overrides each `IRpc` method and calls the corresponding
  native method.

### 3.5 Manifest
```xml
<service android:name=".rpcserverservice" android:enabled="true" android:exported="true">
    <intent-filter>
        <action android:name="rpcserverservice" />
    </intent-filter>
</service>
```
The action name (`rpcserverservice`) is the identifier the client uses to bind.
There is no launcher activity (the application is headless — "Launch options:
Nothing").

### 3.6 Build and install
Run `./gradlew installPhoneDebug` (or `installEmulatorDebug`). aidlserver must be
installed before rpcclient2.

---

## 4. Client Project (rpcclient2)

- Add the **same** `aidl/com/example/aidlserver/` package containing the
  **identical** `IRpc.aidl` and `RpcData.aidl` (byte-for-byte identical to the
  server's, as any mismatch corrupts the parcel), together with the same Gradle AIDL
  configuration.
- In `MainActivity`:
  - Declare `IRpc iRpc;` and a `ServiceConnection` (`onServiceConnected` obtains
    `IRpc.Stub.asInterface(binder)`).
  - Bind by action:
    ```java
    Intent i = new Intent("rpcserverservice");
    i.setPackage("com.example.aidlserver");
    bindService(i, mServiceConnection, BIND_AUTO_CREATE);
    ```
  - UI: select a function, enter arguments, and tap **Call** to invoke
    `iRpc.<method>(…)`. The binder call must run on a **background thread**, as it can
    block awaiting the reply.
- The client contains no `jni/` folder; it uses only the framework, AppCompat, and
  AIDL.
- Two manifest requirements on recent Android versions:
  - `<queries><package android:name="com.example.aidlserver"/></queries>`, otherwise
    the bind is blocked (Android 11+ package visibility) and `iRpc` remains null.
  - `<uses-feature android:name="android.hardware.type.automotive" android:required="false"/>`,
    so the application installs on a phone.

---

## 5. AIDL Argument Marshalling (`RpcData`)

Java cannot pass C structures or pointers over binder, so every non-trivial argument
is carried in a **`Parcelable`** class, `RpcData` (an identical file in both
applications):

| Field | Purpose |
|-------|---------|
| `int x` | a scalar input, or the returned result (`getX()`/`setX()`) |
| `String s` | string payloads |
| `rpcpoint PointStruct {int x, y;}` | the `sPoint` structure |
| `int[] rpcarray` | array payloads |

**The AIDL direction matches the C direction:** `in RpcData` for input-only,
`out RpcData` for a result, and `inout RpcData` for a value that is transmitted and
echoed back (for example, the array and the "Hello …" string).

> **Variable-length arrays:** deserialize with `parcel.createIntArray()`, not
> `new int[N]; parcel.readIntArray(arr)`. `readIntArray` requires the destination to
> be exactly the written length, and otherwise throws `"bad array lengths"`; a fixed
> `new int[4]` succeeds only for four-element arrays. `createIntArray()` reads the
> length prefix and allocates correctly. Correspondingly, avoid fixed-index logging
> such as `arr[0]+arr[1]+arr[2]+arr[3]`, which fails for shorter arrays; use
> `Arrays.toString(arr)`.

When a new argument type is added, extend the fields of `RpcData` together with its
`writeToParcel` / `RpcData(Parcel)` / `readFromParcel` methods, and keep the copy in
both applications identical.

---

## 6. Updating C Files on Function-Set Changes

CORE1 carries its own copy of the generated code, so after
`python src/python_scripts/generator_code.py` is run (from the `RPC_ReferenceFramework`
repository root):

1. Copy the regenerated **`rpc_fncode.h`** (from `inc/`) and
   **`rpc_core1_client.c`, `rpc_core1_server.c`** (from `src/rpc_src/`) into
   `aidlserver/jni/`. Because `FNCODE` values are name-hashed, `rpc_fncode.h` is
   typically unchanged when only an argument is edited.
2. If the phone must **call** a new remote function or **serve** a new CORE1 function
   through the UI, also update:
   - `IRpc.aidl` in both applications (identical);
   - a JNI wrapper in `jni/hello.c`;
   - the `native` declaration and binder method in `rpcserverservice.java`;
   - a spinner entry, a `callFn()` case, and an `updateArgVisibility()` case in
     `rpcclient2`.
3. Rebuild both applications with Gradle. Additions confined to CORE2 and CORE3
   require no Android change.

---

## 7. Running: Client vs TCP Server, Emulator vs Phone

### 7.1 Android as a client (the default; supported on emulator and phone)
1. Start the hub (for example, `./core2 -S` on the PC).
2. In rpcclient2, enter the hub's IP and select **Connect**.
   - Internally: `socketconnect(ip)` → `setServerCore(2)` + `setServerIp(ip)` +
     `startrpc`.
3. Select a function, enter arguments, and select **Call**.

On an **emulator**, use `10.0.2.2` to reach a hub running on the host machine (the
AVD's alias for the host's loopback). On a **phone**, use the hub's LAN IP.

### 7.2 Android as the TCP server / hub (requires a physical phone)
1. Place the phone on the **same Wi-Fi network** as the other cores.
2. In rpcclient2, enable **Server mode** and select **Connect**.
   - Internally: `startServer()` → `setServerCore(1)` + `startrpc`; the phone binds
     the single shared port `8848` and accepts every client on it (each client
     identifies itself with a handshake). No IP is required.
3. The screen displays the device's IP (via `getLocalIpAddress()` — NetworkInterface,
   then WifiManager, then a UDP `getsockname` fallback). Supply that IP to the cores:
   ```
   core2.exe -s <phone_ip>      ./core3 -s <phone_ip>
   ```

### 7.3 Emulator limitation (why server mode requires a phone)
An emulator resides behind the AVD's **NAT** on `10.0.2.x`:
- **Outbound connections succeed** (client mode): the NAT rewrites the phone's
  outbound connection through the host, so a hub observes it arriving from the host's
  IP. This is why Android-as-client works on the emulator.
- **Inbound connections are blocked** (server mode): external cores cannot open a
  connection to `10.0.2.x`. An emulator therefore cannot act as a TCP server for other
  machines without port forwarding:
  ```
  adb forward tcp:8848 tcp:8848
  # cores on the SAME host then connect to 127.0.0.1
  ./core3 -s 127.0.0.1     ./core2 -s 127.0.0.1
  ```
  A physical phone has no such NAT and operates with its LAN IP.

### 7.4 Core-Not-Connected Handling
Before invoking a function, the application checks the owning core's membership
(`iRpc.isCorePresent(coreNum)`, backed by the framework's `rpc_is_core_present`). If
that core is not in the network, the application reports *"CORE3 is NOT connected …"*
rather than blocking on a reply that will never arrive. CORE1 functions execute
locally and are always available.

---

## 8. Build and Install Reference

```bash
# Server engine and native library, then the UI. Install BOTH (phone variant):
cd ~/AndroidStudioProjects/aidlserver && JAVA_HOME=~/Android/jdk17 ./gradlew installPhoneDebug
cd ~/AndroidStudioProjects/rpcclient2 && JAVA_HOME=~/Android/jdk17 ./gradlew installPhoneDebug
# emulator variants: installEmulatorDebug
```

**Sideload verification failure** (`INSTALL_FAILED_VERIFICATION_FAILURE`) on a
physical phone:
```bash
adb shell settings put global verifier_verify_adb_installs 0
```
Additionally disable "Verify apps over USB" / Play Protect; on MIUI/ColorOS, also
enable "Install via USB".

**Log inspection:** `adb logcat | grep -E "RPC_MAIN|rpcservice|MainActivityRPC"`.
aidlserver is headless, so results and native `printf` output appear in logcat rather
than on screen.

---

## 9. Out of Scope

Integrating rpcclient2 into an AOSP build as a built-in car application
(`packages/apps/Car`, `Android.mk`, `car.mk` `PRODUCT_PACKAGES`,
`lunch aosp_car_x86_64_userdebug`) is a separate exercise requiring its own AOSP
tree, and is deliberately outside the scope of the two-application Studio setup
described above.
