#!/bin/sh
set -eu
umask 077

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' "backup.sh must run as root" >&2
    exit 1
fi

DATA_DIR=${DATA_DIR:-/var/lib/agent-history}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/agent-history}
CONTAINER_IMAGE=${CONTAINER_IMAGE:-localhost/agent-history-portal_web:latest}
SOURCE="$DATA_DIR/db.sqlite3"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$BACKUP_DIR/db-$STAMP.sqlite3"

test ! -L "$DATA_DIR"
test ! -L "$BACKUP_DIR"
install -d -m 0700 -o root -g root "$BACKUP_DIR"
if [ ! -f "$SOURCE" ] || [ -L "$SOURCE" ]; then
    printf '%s\n' "source database is missing or is a symlink: $SOURCE" >&2
    exit 1
fi
if [ -e "$TARGET" ]; then
    printf '%s\n' "backup target already exists: $TARGET" >&2
    exit 1
fi
WORK_DIR=$(mktemp -d "$BACKUP_DIR/.work-$STAMP.XXXXXX")
chown 10001:10001 "$WORK_DIR"
chmod 0700 "$WORK_DIR"
TEMP="$WORK_DIR/db.sqlite3"
cleanup() {
    rm -f \
        "$WORK_DIR/db.sqlite3" \
        "$WORK_DIR/db.sqlite3-journal" \
        "$WORK_DIR/db.sqlite3-wal" \
        "$WORK_DIR/db.sqlite3-shm"
    rmdir "$WORK_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

podman run --rm -i \
    --network none \
    --read-only \
    --cap-drop all \
    --security-opt no-new-privileges \
    --user 10001:10001 \
    --pids-limit 64 \
    --memory 256m \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    --entrypoint python \
    -v "$DATA_DIR:/source:ro" \
    -v "$WORK_DIR:/output" \
    "$CONTAINER_IMAGE" \
    - "/source/db.sqlite3" "/output/db.sqlite3" <<'PY'
import os
import sqlite3
import sys

source, target = sys.argv[1:3]
if not os.path.isfile(source) or os.path.islink(source):
    raise SystemExit("source database is missing or is a symlink")

src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(target, timeout=30)
try:
    src.backup(dst)
    result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"backup integrity check failed: {result}")
    dst.commit()
finally:
    dst.close()
    src.close()

with open(target, "rb") as handle:
    os.fsync(handle.fileno())
PY

test -f "$TEMP"
test ! -L "$TEMP"
test -s "$TEMP"
chown root:root "$TEMP"
chmod 600 "$TEMP"
mv "$TEMP" "$TARGET"
rmdir "$WORK_DIR"
trap - EXIT HUP INT TERM
sha256sum "$TARGET"
