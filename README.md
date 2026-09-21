# Electronic Silambam Scoreboard

Landscape Kivy scoreboard for Silambam matches. No database, no server, no
bundled assets — the beep sounds are generated on first launch.

```
silambam/
├── main.py                     the whole app
├── buildozer.spec              APK packaging config
├── test_sender.py              fake ESP32 for testing from a laptop
├── esp32_client/
│   └── esp32_client.ino        Arduino sketch for the real boards
└── .github/workflows/
    └── build-apk.yml           builds the APK on GitHub, no local setup
```

## 1. Try it on a PC first (fastest)

```bash
pip install kivy==2.3.0
python main.py
```

A 1024x576 landscape window opens. Everything works except that the on-screen
keyboard behaves differently to a phone.

## 2. Build the APK

### Option A — GitHub Actions (recommended, nothing to install)

1. Create a new GitHub repo and push these files (keep the folder structure).
2. Open the **Actions** tab → **Build APK** → **Run workflow**.
3. Wait ~25–40 min for the first run (it downloads the Android SDK/NDK).
   Later runs are cached and take ~5 min.
4. Download the `silambam-apk` artifact, unzip, install the `.apk` on the phone.

### Option B — locally on Linux / WSL2 / Ubuntu

```bash
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool \
     pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev ccache
pip install --upgrade "cython<3.0" virtualenv buildozer
cd silambam
buildozer -v android debug          # APK lands in bin/
adb install -r bin/*.apk
```

Buildozer does **not** work on Windows directly — use WSL2 or Option A.

## 3. Using the app

| Action | Result |
|---|---|
| Tap inside a player card | +1 touch, card flashes, timer keeps running |
| Tap anywhere else (margins, title, timer area) | pauses, RESUME popup appears |
| `TOUCH +1` / `PENALTY -1` buttons | manual entry by the scorer |
| `START` | starts the round (becomes PAUSE / RESUME) |
| `End Round` | ends the current round early |
| `NEW MATCH` | asks to confirm, then clears everything |
| `SETTINGS` | change round duration (5–3600 s) |

Rules baked in:

* Hits and penalties are accepted **only while the timer is running**. Anything
  sent while paused/idle is ignored and the centre shows "Timer is not running".
* 2 rounds of 60 s by default. Round 1 → short popup → round 2 starts
  automatically, no button press needed.
* If the score is level after round 2 a **round 3 decider** is added
  automatically.
* A penalty adds 1 to the penalty counter and removes 1 point (floored at 0 —
  change `PENALTY_FLOOR_ZERO = False` in `main.py` if you want negatives).

## 4. ESP32 setup

The app runs a TCP server on port **5005**. The bottom of the centre panel shows
the phone's IP and the number of connected boards (it turns green when a board
is connected).

1. Phone and ESP32 must be on the **same WiFi** (a phone hotspot works — then
   use the hotspot IP shown in the app).
2. Put that IP into `SCOREBOARD_IP` in the sketch, set `PLAYER` to `"A"` or
   `"B"`, flash one board per player.
3. Commands accepted, one per line: `A:HIT`, `B:HIT`, `A:PENALTY`, `B:PENALTY`
   (`TOUCH` and `FOUL` are accepted as aliases). Anything else is ignored, so a
   noisy serial line can't corrupt the score.

Before the hardware exists, test the link with `python test_sender.py <phone-ip>`.

## 5. Tuning

All at the top of `main.py`:

```python
DEFAULT_ROUND_SECONDS = 60
ROUNDS_PER_MATCH      = 2
PENALTY_FLOOR_ZERO    = True
TCP_PORT              = 5005
```

Colours are the block of constants right underneath.
