#!/usr/bin/env bash
# One-time move of the dashboard data from SQLite to its own database on aidolab-db (RDS).
# Run on the gateway as root: sudo bash /srv/aidolab/app/deploy/move-db-to-rds.sh
# The password is generated here and written only to /etc/aidolab/env (never printed).
# The SQLite file stays as it is; on any error DATABASE_URL is removed again (= back to SQLite).
set -euo pipefail
APP=/srv/aidolab/app
PY=/srv/aidolab/venv/bin/python
ENVF=/etc/aidolab/env
DATA=/srv/aidolab/data
DUMP="$DATA/move-to-rds.json"

if grep -q '^DATABASE_URL=' "$ENVF"; then
  echo "DATABASE_URL аль хэдийн байна — алгаслаа."; exit 0
fi
as_app() { sudo -u aidolab env $(grep -v '^#' "$ENVF" | xargs) "$@"; }
counts() {
  as_app "$PY" manage.py shell -c "
from django.apps import apps
for m in sorted(apps.get_models(), key=lambda m: m._meta.label):
    print(m._meta.label, m.objects.count())" 2>/dev/null | grep -v 'objects imported'
}
rollback() {
  echo "!! Алдаа гарлаа — SQLite руу буцааж байна"
  sed -i '/^DATABASE_URL=/d' "$ENVF"
  rm -f "$DUMP"
  systemctl start aidolab aidolab-worker
}

cd "$APP"
systemctl stop aidolab aidolab-worker   # no writes while copying
trap rollback ERR

counts > "$DATA/counts-sqlite.txt"
install -m 600 -o aidolab -g aidolab /dev/null "$DUMP"   # holds env var values etc.
as_app "$PY" manage.py dumpdata --natural-foreign -e contenttypes -e auth.permission -o "$DUMP"

URL=$(env $(grep -v '^#' "$ENVF" | xargs) DJANGO_SETTINGS_MODULE=config.settings "$PY" - <<'PYEOF'
import secrets
import django
django.setup()
from psycopg import sql
from checker import aws
password = secrets.token_urlsafe(32)
with aws._admin_conn() as conn:
    conn.execute(sql.SQL("CREATE ROLE aidolab_web LOGIN PASSWORD {}").format(sql.Literal(password)))
    conn.execute("GRANT aidolab_web TO CURRENT_USER")  # RDS: needed to own the DB
    conn.execute("CREATE DATABASE aidolab_dashboard OWNER aidolab_web")
    # Student app roles live on the same server: they must not even connect here.
    conn.execute("REVOKE ALL ON DATABASE aidolab_dashboard FROM PUBLIC")
print(f"postgresql://aidolab_web:{password}@{aws.cfg('DB_HOST')}:5432/aidolab_dashboard")
PYEOF
)
echo "DATABASE_URL=$URL" >> "$ENVF"
unset URL

as_app "$PY" manage.py migrate --noinput -v0
as_app "$PY" manage.py loaddata "$DUMP" -v0
counts > "$DATA/counts-rds.txt"
if ! diff "$DATA/counts-sqlite.txt" "$DATA/counts-rds.txt"; then
  echo "!! Мөрийн тоо таарахгүй байна"; false
fi
trap - ERR
rm -f "$DUMP"
systemctl start aidolab aidolab-worker
echo "Дууслаа: dashboard одоо RDS (aidolab_dashboard) ашиглаж байна. SQLite файл хэвээр үлдлээ."
