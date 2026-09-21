[app]

title = Silambam Scoreboard
package.name = silambam
package.domain = org.silambam

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0.0

# kivy pulls in sdl2 / audio automatically
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0

orientation = landscape
fullscreen = 1

# keep the screen awake during a match
android.wakelock = True

# needed for the ESP32 TCP server on port 5005
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,WAKE_LOCK

android.api = 34
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a,armeabi-v7a
android.allow_backup = True

# allow plain (non-TLS) sockets on newer Android
android.manifest.application_arguments = android:usesCleartextTraffic="true"

# presplash / icon are optional - drop PNGs next to main.py and uncomment
#icon.filename = %(source.dir)s/icon.png
#presplash.filename = %(source.dir)s/presplash.png

[buildozer]

log_level = 2
warn_on_root = 1
