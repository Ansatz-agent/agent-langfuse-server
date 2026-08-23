#!/bin/sh
set -eu

BACKUP=${1:?usage: restore-verify.sh /path/to/backup.sqlite3}
python3 - "$BACKUP" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"integrity check failed: {result}")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required = {"history_historysession", "history_historymessage", "history_importbatch"}
    missing = required - tables
    if missing:
        raise SystemExit(f"missing tables: {sorted(missing)}")
    print("restore verification: ok")
finally:
    connection.close()
PY
