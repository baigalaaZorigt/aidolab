#!/usr/bin/env bash
# One-time (re-runnable) setup of the aidolab gateway on a fresh Ubuntu 24.04 arm64 EC2 instance.
#   sudo REPO_URL=git@github.com:<org>/<repo>.git bash bootstrap.sh
# First run creates a GitHub deploy key and stops; add it to the repo (read-only), then run again.
set -euo pipefail
: "${REPO_URL:?REPO_URL-ийг заана уу (git@github.com:...)}"
HOME_DIR=/srv/aidolab

if ! command -v caddy >/dev/null; then
  apt-get update
  apt-get install -y python3.12-venv git curl gpg debian-keyring debian-archive-keyring apt-transport-https unattended-upgrades
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

id aidolab >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /usr/sbin/nologin aidolab
install -d -o aidolab -g aidolab "$HOME_DIR/data" "$HOME_DIR/.ssh"

KEY="$HOME_DIR/.ssh/id_ed25519"
if [ ! -f "$KEY" ]; then
  sudo -u aidolab ssh-keygen -t ed25519 -N "" -C "aidolab-gateway" -f "$KEY" >/dev/null
  sudo -u aidolab sh -c "ssh-keyscan github.com >> $HOME_DIR/.ssh/known_hosts 2>/dev/null"
  echo
  echo "GitHub repo → Settings → Deploy keys → Add (Read-only) дээр доорх түлхүүрийг нэмээд скриптийг дахин ажиллуулна уу:"
  cat "$KEY.pub"
  exit 0
fi

if [ ! -d "$HOME_DIR/app/.git" ]; then
  sudo -u aidolab git clone "$REPO_URL" "$HOME_DIR/app"
fi

if [ ! -f /etc/aidolab/env ]; then
  install -d -m 750 -g aidolab /etc/aidolab
  secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
  sed "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$secret|" "$HOME_DIR/app/deploy/env.example" > /etc/aidolab/env
  chown root:aidolab /etc/aidolab/env
  chmod 640 /etc/aidolab/env
  echo "/etc/aidolab/env үүслээ. ANTHROPIC_API_KEY, ACCESS_CODE-оо бөглөөд 'sudo bash deploy/update.sh' ажиллуулна уу."
fi

install -m 644 "$HOME_DIR/app/deploy/aidolab.service" /etc/systemd/system/aidolab.service
install -m 644 "$HOME_DIR/app/deploy/aidolab-backup.service" /etc/systemd/system/aidolab-backup.service
install -m 644 "$HOME_DIR/app/deploy/aidolab-backup.timer" /etc/systemd/system/aidolab-backup.timer
install -m 644 "$HOME_DIR/app/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable aidolab aidolab-backup.timer caddy

bash "$HOME_DIR/app/deploy/update.sh"
