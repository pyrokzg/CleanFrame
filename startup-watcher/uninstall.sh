#!/usr/bin/env bash
# Remove the CleanFrame startup watcher service.
#
#   sudo ./uninstall.sh           keep the saved TV token in /var/lib/cleanframe-watcher
#   sudo ./uninstall.sh --purge   also delete the token and the service account
set -euo pipefail

NAME=cleanframe-watcher PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" = 0 ] || { echo "Error: run with sudo." >&2; exit 1; }

systemctl disable --now "$NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/$NAME.service"
systemctl daemon-reload
rm -rf "/opt/$NAME"
rm -f "/etc/default/$NAME"
if [ "$PURGE" = 1 ]; then
  rm -rf "/var/lib/$NAME" "/var/lib/private/$NAME"
  id "$NAME" >/dev/null 2>&1 && userdel "$NAME"
fi
echo "Removed $NAME.$([ "$PURGE" = 1 ] && echo ' Token and account deleted.' || echo ' The TV token is kept in /var/lib/'"$NAME"'.')"
echo "The TV itself was never changed; you can also remove \"CleanFrame watcher\" from the TV's Device Connection Manager list."
