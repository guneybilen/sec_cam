#!/bin/bash

# Restrict default permissions: files created are readable/writable only by you
umask 077

# Take filename from the first argument
FILE="$1"
if [ -z "$FILE" ]; then
    echo "Usage: $0 <filename>"
    exit 1
fi

# Use a private lock directory under /run/user/$UID
LOCKDIR="/run/user/$UID/secure-edit"
mkdir -p "$LOCKDIR"

LOCKFILE="$LOCKDIR/$(basename "$FILE").lock"
OWNERFILE="${LOCKFILE}.owner"
LOGFILE="$LOCKDIR/$(basename "$FILE").attempts.log"

# Track tail PID globally
TAIL_PID=""

# Unified trap: kill tail (if running), re-lock file, and clean owner info
cleanup() {
    # Stop live monitoring if started
    if [ -n "$TAIL_PID" ] && kill -0 "$TAIL_PID" 2>/dev/null; then
        kill "$TAIL_PID" 2>/dev/null
    fi

    # Re-lock the file (will prompt for your security key)
    echo "Re-locking $FILE..."
    if ! sudo chattr +i "$FILE"; then
        echo "ERROR: Failed to re-lock $FILE with chattr +i" >&2
    else
        echo "$FILE successfully re-locked."
    fi

    # Remove owner metadata
    rm -f "$OWNERFILE"
}
# Run cleanup on normal exit and common termination signals
trap cleanup EXIT INT TERM HUP

CURRENT_USER=$(whoami)
CURRENT_TTY=$(tty)
CURRENT_PID=$$
CURRENT_HOST=$(hostname)

# Acquire exclusive lock
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    if [ -f "$OWNERFILE" ]; then
        ATTEMPT_INFO="[$(date)] user=$CURRENT_USER tty=$CURRENT_TTY pid=$CURRENT_PID host=$CURRENT_HOST tried to edit $FILE"
        echo "$ATTEMPT_INFO" >> "$LOGFILE"
        echo "Another editing session is already in progress for $FILE."
        echo "Lock held by: $(cat "$OWNERFILE")"
    else
        echo "Another editing session is already in progress for $FILE (unknown owner)."
    fi
    exit 1
fi

# Record lock owner
echo "user=$CURRENT_USER tty=$CURRENT_TTY pid=$CURRENT_PID host=$CURRENT_HOST" > "$OWNERFILE"

# Start monitoring attempts in the background
touch "$LOGFILE"
tail -f "$LOGFILE" &
TAIL_PID=$!

# 1. Remove immutability (tap key here)
if ! sudo chattr -i "$FILE"; then
    echo "ERROR: Failed to unlock $FILE with chattr -i" >&2
    exit 1
fi

# 2. Open in your editor
gedit "$FILE"

# On exit from nano, cleanup() runs via trap:
# - tail is killed (if running)
# - file is re-locked with chattr +i (with error reporting)
# - owner info is removed
