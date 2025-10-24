#!/bin/bash
umask 077

FILE="$1"
if [ -z "$FILE" ]; then
    echo "Usage: $0 <filename>"
    exit 1
fi

LOCKDIR="/run/user/$UID/secure-edit"
mkdir -p "$LOCKDIR"

LOCKFILE="$LOCKDIR/$(basename "$FILE").lock"
OWNERFILE="${LOCKFILE}.owner"
LOGFILE="$LOCKDIR/$(basename "$FILE").attempts.log"
TAIL_PID=""

cleanup() {
    if [ -n "$TAIL_PID" ] && kill -0 "$TAIL_PID" 2>/dev/null; then
        kill "$TAIL_PID" 2>/dev/null
    fi

    echo "Re-locking $FILE..."
    if ! sudo /usr/local/bin/chattr-lock +i "$FILE"; then
        echo "ERROR: Failed to re-lock $FILE with chattr +i" >&2
    else
        echo "$FILE successfully re-locked."
    fi

    rm -f "$OWNERFILE"
}
trap cleanup EXIT INT TERM HUP

CURRENT_USER=$(whoami)
CURRENT_TTY=$(tty)
CURRENT_PID=$$
CURRENT_HOST=$(hostname)

exec 200>"$LOCKFILE"
if ! flock -n 200; then
    if [ -f "$OWNERFILE" ]; then
        echo "[$(date)] user=$CURRENT_USER tty=$CURRENT_TTY pid=$CURRENT_PID host=$CURRENT_HOST tried to edit $FILE" >> "$LOGFILE"
        echo "Another editing session is already in progress for $FILE."
        echo "Lock held by: $(cat "$OWNERFILE")"
    else
        echo "Another editing session is already in progress for $FILE (unknown owner)."
    fi
    exit 1
fi

echo "user=$CURRENT_USER tty=$CURRENT_TTY pid=$CURRENT_PID host=$CURRENT_HOST" > "$OWNERFILE"

touch "$LOGFILE"
tail -f "$LOGFILE" &
TAIL_PID=$!

if ! sudo chattr -i "$FILE"; then
    echo "ERROR: Failed to unlock $FILE with chattr -i" >&2
    exit 1
fi

gedit "$FILE"

