#!/bin/sh
# Start as root just long enough to make the data folders writable, then drop to PUID:PGID.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
  for d in "$DATA_DIR" "${RECEIPTS_LOCAL_DIR:-}"; do
    [ -n "$d" ] || continue
    mkdir -p "$d"
    # only fix ownership when it's wrong (cheap on big receipt folders)
    if [ "$(stat -c %u "$d")" != "$PUID" ]; then
      chown -R "$PUID:$PGID" "$d" 2>/dev/null || echo "warning: couldn't chown $d (read-only or NFS?)"
    fi
  done
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi
exec "$@"
