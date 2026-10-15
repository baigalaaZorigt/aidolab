#!/usr/bin/env bash
# One-time (re-runnable) setup of the aidolab gateway on a fresh Ubuntu 24.04 EC2 instance.
#   sudo REPO_URL=https://github.com/baigalaaZorigt/aidolab.git bash bootstrap.sh
# For a private repo use a git@github.com: URL; the first run then creates a GitHub
# deploy key and stops — add it to the repo (read-only), then run again.
set -euo pipefail
: "${REPO_URL:?REPO_URL-ийг заана уу (https://github.com/... эсвэл git@github.com:...)}"
HOME_DIR=/srv/aidolab

if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
  apt-get update
  apt-get install -y python3.12-venv git curl nginx certbot python3-certbot-nginx unattended-upgrades
fi

id aidolab >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /usr/sbin/nologin aidolab
install -d -m 750 -o aidolab -g aidolab "$HOME_DIR/data" "$HOME_DIR/.ssh"
# nginx (www-data) must reach app/staticfiles: allow traversal only, data/ stays private.
chmod 711 "$HOME_DIR"

KEY="$HOME_DIR/.ssh/id_ed25519"
if [[ "$REPO_URL" == git@* ]] && [ ! -f "$KEY" ]; then
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
# Install the nginx site only once: certbot edits it in place to add TLS.
if [ ! -f /etc/nginx/sites-available/aidolab ]; then
  install -m 644 "$HOME_DIR/app/deploy/nginx-aidolab.conf" /etc/nginx/sites-available/aidolab
  ln -sf /etc/nginx/sites-available/aidolab /etc/nginx/sites-enabled/aidolab
  rm -f /etc/nginx/sites-enabled/default
fi
systemctl daemon-reload
systemctl enable aidolab aidolab-backup.timer nginx

bash "$HOME_DIR/app/deploy/update.sh"
# HTTPS: Let's Encrypt via certbot (renewal runs from certbot.timer). Needs DNS → this IP.
if [ ! -d /etc/letsencrypt/live/app.aidolab.mn ]; then
  certbot --nginx -d app.aidolab.mn --non-interactive --agree-tos --register-unsafely-without-email --redirect
fi
