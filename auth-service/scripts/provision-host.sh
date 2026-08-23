#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' "provision-host.sh must run as root" >&2
    exit 1
fi

DATA_DIR=${DATA_DIR:-/var/lib/agent-history}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/agent-history}
APP_UID=${APP_UID:-10001}
APP_GID=${APP_GID:-10001}

case "$APP_UID:$APP_GID" in
    *[!0-9:]* | :* | *:)
        printf '%s\n' "APP_UID and APP_GID must be numeric" >&2
        exit 1
        ;;
esac

test ! -L "$DATA_DIR"
install -d -m 0700 -o "$APP_UID" -g "$APP_GID" "$DATA_DIR"

test ! -L "$BACKUP_DIR"
install -d -m 0700 -o root -g root "$BACKUP_DIR"
