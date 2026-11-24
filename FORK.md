# About this fork

This is a fork of [nthnclrk/CleanFrame](https://github.com/nthnclrk/CleanFrame) (MIT). The CleanFrame app, its tools and docs are unchanged. The fork adds one thing: a startup watcher that opens CleanFrame whenever the TV turns on.

## Why

Samsung TVs can autorun the last app, but they have no setting for a fixed startup app. Upstream's route to a fixed startup app is the optional native Home/startup helper. That helper writes undocumented TV settings, needs a recovery baseline and depends on the firmware.

The watcher takes a different route. It runs on another always-on Linux machine on the same network, such as a home server or a Raspberry Pi, and only uses the TV's local remote-control API. It changes no TV settings, and removing the service undoes it completely.

## Changes from upstream

| Path | Change |
|---|---|
| [`startup-watcher/cleanframe_watcher.py`](startup-watcher/cleanframe_watcher.py) | New. The watcher. Python 3.7+, standard library only. |
| [`startup-watcher/install.sh`](startup-watcher/install.sh), [`uninstall.sh`](startup-watcher/uninstall.sh) | New. Install or remove it as a systemd service, with a dry-run mode. |
| [`startup-watcher/cleanframe-watcher.service`](startup-watcher/cleanframe-watcher.service) | New. Hardened systemd unit: a locked system account, a read-only system, and one writable folder for the TV token. |
| [`startup-watcher/test_watcher.py`](startup-watcher/test_watcher.py) | New. Offline tests (12) for the WebSocket framing, token storage and power-on decisions. No TV needed. |
| [`startup-watcher/README.md`](startup-watcher/README.md) | New. Install, manage, tune and remove the watcher. |
| `README.md` | A short note at the top pointing here. |
| `FORK.md` | This file. |

## How the watcher works

```
           every 3 s: GET http://TV:8001/api/v2/  ->  PowerState
                               |
      first check -------------+---- off/standby -> on
      leave the screen alone   |
                               v
               wait 4 s for the TV to settle
                               |
          +--------------------+---------------------+
          | up to 4 attempts:                        |
          |   TV no longer on?         -> stop        |
          |   CleanFrame on screen?    -> stop        |
          |   POST /api/v2/applications/APP_ID       |
          |     (last attempt: paired WebSocket)     |
          |   check visibility every 0.5 s for 4 s   |
          |     seen on screen once    -> stop        |
          +------------------------------------------+
```

1. **Power detection.** The TV's REST API on port 8001 answers while the TV is on or in network standby, and its `device.PowerState` field says which. No answer counts as off. The watcher acts only on a change to `on`, so a TV that is already on when the watcher starts (after a reboot, for example) is left alone.
2. **Settle.** Right after power-on the TV briefly shows Samsung Home or the last app and may ignore requests, so the watcher waits a few seconds first.
3. **Launch.** It asks the TV to open the app with `POST /api/v2/applications/APP_ID`. On the tested TV this brings an already-running web app to the front.
4. **Confirm.** It reads `GET /api/v2/applications/APP_ID`, whose `visible` field says whether the app is in front, about every half second. Once CleanFrame has been seen on screen, the watcher is done for that power-on. A source or app picked afterwards is the viewer's choice and is never undone.
5. **Retry and fallback.** If CleanFrame never appears, it tries again up to four times in all. The last try uses Samsung's remote-control WebSocket (`wss://TV:8002`, `ed.apps.launch` with `NATIVE_LAUNCH`), in case the TV accepts REST requests but ignores them. That channel needs a one-time **Allow** on the TV; the returned token is saved with owner-only permissions. If pairing is denied, the watcher stops rather than retrying.
6. **TVs that don't report visibility.** If the TV never answers the visibility check, one accepted request is treated as success, so the watcher can't keep relaunching over the viewer.

## Tested

- One 2020 Samsung QLED (Q95T series) running the CleanFrame web app, with the watcher on an Ubuntu 26.04 server. Startup, the shorter timings in the watcher README, and leaving a source picked after CleanFrame appears were checked on that TV.
- Other models, The Frame included, have not been tested with the watcher. The REST and WebSocket calls are the ones Samsung's 2016+ Tizen TVs commonly expose, but check yours with `--status` and `--launch-now` before installing the service.

## Limits

- Samsung Home or the last app shows for a few seconds before CleanFrame opens.
- The TV does not report which source is selected. A source picked before CleanFrame has appeared is replaced once by CleanFrame.
- It also opens CleanFrame when an HDMI-CEC device, such as a console, wakes the TV.
- It only works while the machine running it is on and can reach the TV. Give the TV a fixed address with a DHCP reservation.

## Licence

The watcher is released under the same MIT licence as upstream CleanFrame; see [LICENSE](LICENSE).
