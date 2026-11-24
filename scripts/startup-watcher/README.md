# Startup watcher

Samsung TVs offer "Autorun Last App" but no fixed startup app. This small service runs on an always-on Linux machine on the same LAN, such as an Ubuntu home server or a Raspberry Pi, and opens CleanFrame each time the TV turns on.

It changes no TV settings. It polls the TV's local REST API (`http://TV:8001/api/v2/`) for `PowerState`. On a standby/off → on change it waits a few seconds, then asks the TV to open the app with `POST http://TV:8001/api/v2/applications/APP_ID`. After each request it checks the app's reported visibility about every half second. Once CleanFrame has been seen on screen it stops, so picking a source or another app afterwards is left alone. If CleanFrame never appears it retries a few times; the last retry uses Samsung's paired remote-control WebSocket (`wss://TV:8002`, `NATIVE_LAUNCH`). On the tested 2020 Q95T, the REST call brought CleanFrame to the front, while the WebSocket `DEEP_LINK` request was accepted but left it in the background. If the watcher starts while the TV is already on, it leaves the screen alone. Python 3.7+, standard library only.

**Limits:** Samsung Home or the last app shows for a few seconds before CleanFrame opens. It runs only while its machine is on and reachable. It also opens CleanFrame when an HDMI-CEC device (such as a console) wakes the TV. The TV does not report the selected source, so a source picked before CleanFrame has appeared (roughly the first 3 to 5 seconds) is replaced once by CleanFrame. To undo it, remove the service; the TV is unchanged.

## Install as a systemd service (Ubuntu, Debian, Raspberry Pi OS)

Put the repository, or just this folder with its files together, on the always-on machine. Turn the TV on, then run the installer from this folder:

```sh
cd CleanFrame/scripts/startup-watcher
sudo bash ./install.sh --tv TV_IP --app PACKAGE.CleanFrame
```

`TV_IP` is the TV's LAN address. `PACKAGE.CleanFrame` stands for your own application ID: the `id` of `<tizen:application>` in your signed build's `app/config.xml`, which is ten characters, a dot and a name (upstream's sample is `nClrkHome1.FrameHome`). You can confirm both with `curl http://TV_IP:8001/api/v2/applications/PACKAGE.CleanFrame`, which returns the app's name while the TV is on. Calling the scripts through `bash` works even when a copy (from a USB drive or a zip, for example) has lost its executable bit.

The installer:

- creates a locked system account, `cleanframe-watcher` (no login, no sudo);
- installs the watcher to `/opt/cleanframe-watcher/` and its settings to `/etc/default/cleanframe-watcher`;
- installs the hardened unit `cleanframe-watcher.service`. It can write only `/var/lib/cleanframe-watcher`, where the TV token is kept (mode 0700);
- if the TV is on, shows the one-time **Allow** prompt on the TV (choose Allow within 60 seconds);
- enables and starts the service, so it also starts after every reboot.

`--dry-run` prints each step without changing anything. `--no-pair` skips the prompt; REST launching works without it, but the WebSocket fallback needs the token. To pair later, turn the TV on and run `sudo bash ./install.sh --tv TV_IP --app PACKAGE.CleanFrame --pair-only`.

Give the TV a fixed address (a DHCP reservation in the router) so `CLEANFRAME_TV` stays valid. To change it, edit `/etc/default/cleanframe-watcher`, then `sudo systemctl restart cleanframe-watcher`.

## Check and manage

```sh
systemctl status cleanframe-watcher
journalctl -u cleanframe-watcher -f        # live log: "TV is now on", "CleanFrame is on screen"
sudo systemctl restart cleanframe-watcher
python3 /opt/cleanframe-watcher/cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --token-file /dev/null --status
```

Remove it with `sudo bash ./uninstall.sh`, or `sudo bash ./uninstall.sh --purge` to also delete the token and the service account. The TV keeps "CleanFrame watcher" in Settings → General → External Device Manager → Device Connection Manager until you remove it there.

If the Allow prompt never appears or was denied, check that same Device Connection Manager screen (Access Notification / Device List).

## Tune the delay

By default the watcher checks the TV every 3 seconds and waits 4 seconds after power-on before the first launch request. On the tested TV the defaults left Samsung Home on screen for about 2 seconds, and values 30% shorter also worked. Override them without editing the installed files:

```sh
sudo mkdir -p /etc/systemd/system/cleanframe-watcher.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/bin/python3 /opt/cleanframe-watcher/cleanframe_watcher.py --settle 2.8 --interval 2.1\n' \
  | sudo tee /etc/systemd/system/cleanframe-watcher.service.d/timing.conf
sudo systemctl daemon-reload && sudo systemctl restart cleanframe-watcher
```

A shorter `--settle` can reach the TV before it is ready; the watcher then needs extra requests. Delete `timing.conf` and repeat the last line to go back to the defaults.

## Run it by hand

```sh
python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --pair        # token in ~/.config/cleanframe-watcher/
python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --status
python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --launch-now
python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame               # watch until Ctrl+C
```

## Tests

`python3 -B test_watcher.py` runs offline checks for WebSocket framing, token storage and the power-on decisions.
