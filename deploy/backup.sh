#!/usr/bin/env bash
# Nightly consistent copy of the SQLite database (run by aidolab-backup.timer).
# EBS snapshots (DLM) then carry these files off the instance.
set -euo pipefail
# After deploy/move-db-to-rds.sh the data lives in RDS, which has its own 7-day automated backups.
if grep -q '^DATABASE_URL=' /etc/aidolab/env; then exit 0; fi
DB=/srv/aidolab/data/db.sqlite3
DIR=/srv/aidolab/data/backups
mkdir -p "$DIR"
/srv/aidolab/venv/bin/python - "$DB" "$DIR/db-$(date +%Y%m%d).sqlite3" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
# Keep two weeks.
find "$DIR" -name 'db-*.sqlite3' -mtime +14 -delete
