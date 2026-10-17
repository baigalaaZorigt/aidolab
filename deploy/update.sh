#!/usr/bin/env bash
# Pull the latest code and restart. Run on the server: sudo bash /srv/aidolab/app/deploy/update.sh
set -euo pipefail
HOME_DIR=/srv/aidolab
APP="$HOME_DIR/app"
as_app() { sudo -u aidolab env $(grep -v '^#' /etc/aidolab/env | xargs) "$@"; }

sudo -u aidolab git -C "$APP" pull --ff-only
[ -d "$HOME_DIR/venv" ] || sudo -u aidolab python3.12 -m venv "$HOME_DIR/venv"
sudo -u aidolab "$HOME_DIR/venv/bin/pip" install -q -r "$APP/requirements.txt"
cd "$APP"
as_app "$HOME_DIR/venv/bin/python" manage.py migrate --noinput
as_app "$HOME_DIR/venv/bin/python" manage.py collectstatic --noinput -v0
as_app "$HOME_DIR/venv/bin/python" manage.py check --deploy --fail-level ERROR
systemctl restart aidolab
if systemctl is-enabled --quiet aidolab-worker 2>/dev/null; then
  bash "$APP/deploy/bootstrap-hosting.sh"  # idempotent: refreshes nginx snippets, units, sync-domains.py
  systemctl restart aidolab-worker
fi
nginx -t && systemctl reload nginx
echo "Шинэчлэгдлээ: https://app.aidolab.mn"
