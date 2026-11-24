#!/usr/bin/env bash
# Install the CleanFrame startup watcher as a systemd service (Ubuntu, Debian, Raspberry Pi OS).
#
#   sudo ./install.sh --tv TV_IP --app PACKAGE.CleanFrame
#
# Options:
#   --no-pair    skip the one-time Allow prompt (REST launch still works; the WebSocket fallback needs it)
#   --pair-only  only repeat the Allow prompt for an installed service
#   --dry-run    print what would be done without changing anything
set -euo pipefail

TV="" APP="" PAIR=1 DRY=0 PAIR_ONLY=0
NAME=cleanframe-watcher
PREFIX=/opt/$NAME
STATE=/var/lib/$NAME
CONFIG=/etc/default/$NAME
UNIT=/etc/systemd/system/$NAME.service
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --tv) TV="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    --no-pair) PAIR=0; shift ;;
    --pair-only) PAIR_ONLY=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 2 ;;
  esac
done

run() { if [ "$DRY" = 1 ]; then printf '+ %s\n' "$*"; else "$@"; fi; }
fail() { echo "Error: $*" >&2; exit 1; }

[ -n "$TV" ] && [ -n "$APP" ] || fail "--tv and --app are required."
[[ "$TV" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "--tv must be an IPv4 address, for example 192.168.1.50."
[[ "$APP" =~ ^[A-Za-z0-9]{10}\.[A-Za-z0-9_-]{1,52}$ ]] || fail "--app must look like ABCDE12345.CleanFrame."
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || fail "Run with sudo."
for f in cleanframe_watcher.py cleanframe-watcher.service README.md; do
  [ -f "$HERE/$f" ] || fail "Missing $f next to install.sh."
done
command -v systemctl >/dev/null || [ "$DRY" = 1 ] || fail "systemd is required."
command -v python3 >/dev/null || fail "python3 is required (sudo apt install python3)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' || fail "Python 3.7 or later is required."

pair() {
  echo "Pairing: choose Allow for \"CleanFrame watcher\" on the TV within 60 seconds."
  run runuser -u "$NAME" -- env CLEANFRAME_TOKEN_FILE="$STATE/tv-token.json" \
    python3 "$PREFIX/cleanframe_watcher.py" --tv "$TV" --app "$APP" --pair
}
if [ "$PAIR_ONLY" = 1 ]; then
  [ "$DRY" = 1 ] || [ -f "$PREFIX/cleanframe_watcher.py" ] || fail "The service is not installed yet."
  pair
  exit 0
fi

echo "Installing $NAME for TV $TV, app $APP"

# A locked system account; the service needs no login, home folder or sudo.
if ! id "$NAME" >/dev/null 2>&1; then
  run useradd --system --no-create-home --home-dir "$STATE" --shell /usr/sbin/nologin "$NAME"
fi
run install -d -m 0755 "$PREFIX"
run install -m 0644 "$HERE/cleanframe_watcher.py" "$HERE/README.md" "$PREFIX/"
run install -d -m 0700 -o "$NAME" -g "$NAME" "$STATE"

if [ "$DRY" = 1 ]; then
  printf '+ write %s\n' "$CONFIG"
else
  umask 022
  printf 'CLEANFRAME_TV=%s\nCLEANFRAME_APP=%s\nCLEANFRAME_TOKEN_FILE=%s/tv-token.json\n' "$TV" "$APP" "$STATE" > "$CONFIG"
fi
run install -m 0644 "$HERE/cleanframe-watcher.service" "$UNIT"
run systemctl daemon-reload

# Check the TV answers before pairing; a TV in deep standby will not show the prompt.
SRC="$PREFIX/cleanframe_watcher.py"; [ "$DRY" = 1 ] && SRC="$HERE/cleanframe_watcher.py"
POWER="$(python3 "$SRC" --tv "$TV" --app "$APP" --token-file /dev/null --status 2>/dev/null || true)"
echo "TV status: ${POWER:-unreachable}"

LATER="sudo $HERE/install.sh --tv $TV --app $APP --pair-only"
if [ "$PAIR" = 1 ]; then
  if [[ "$POWER" == *'"power": "on"'* ]]; then
    pair || echo "Pairing did not complete. The service still launches through REST; pair later with: $LATER"
  else
    echo "The TV is not on, so pairing was skipped. Turn it on, then run: $LATER"
  fi
fi

run systemctl enable --now "$NAME"
echo
echo "Installed. Follow the log with: journalctl -u $NAME -f"
echo "Remove with: sudo $HERE/uninstall.sh"
