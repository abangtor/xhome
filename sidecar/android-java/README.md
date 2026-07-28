# XHome Android Native Helper

This is the smallest helper needed to drive XHome's original Android native
streaming library. It is intentionally not a Home Assistant add-on yet.

The helper:

- declares `com.lancens.api.IVIEWSAVAPIs` with the same JNI method names as the
  app
- loads `libIVIEWSAVAPIs.so`
- starts `IVIEWSAVAPIs.start(uid, token, callback)`
- reads JSON command lines from stdin
- writes callback records to stdout using the `XHF1` protocol documented in
  `docs/XHOME_LIVE_SIDECAR.md`

## Build Syntax Check

On a normal development machine:

```bash
javac -d /tmp/xhome-native-helper \
  sidecar/android-java/src/com/lancens/api/IVIEWSAVAPIs.java \
  sidecar/android-java/src/xhome/NativeHelper.java
```

That only checks Java syntax. The helper must run on Android/ARM64 with
`libIVIEWSAVAPIs.so` available in `java.library.path`.

## Runtime Shape

The intended runtime command is roughly:

```bash
dalvikvm \
  -Djava.library.path=/path/to/native-libs/arm64-v8a \
  -cp /path/to/xhome-native-helper.jar \
  xhome.NativeHelper
```

Then run the Python relay against that command:

```bash
python -m xhome.live_sidecar relay \
  --uid LSV... \
  --token ... \
  --native-iot-host usaiotd.lancens.com \
  --bridge-command "adb shell dalvikvm -Djava.library.path=/data/local/tmp/xhome-libs -cp /data/local/tmp/xhome-native-helper.jar xhome.NativeHelper" \
  --h264-out /tmp/xhome.h264 \
  --duration 30
```

The `adb shell ...` form may need a small wrapper script because stdin/stdout
binary piping through adb can be fussy. Annoyingly fussy, because of course it
can.
