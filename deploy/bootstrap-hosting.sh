#!/usr/bin/env bash
# Second step on the gateway built by infra/ (after bootstrap.sh): turns on real hosting.
#   1. Append `terraform output -raw gateway_env` to /etc/aidolab/env
#   2. sudo bash /srv/aidolab/app/deploy/bootstrap-hosting.sh
# Gets the *.aidolab.mn wildcard certificate (DNS-01 via Route 53, using the instance role),
# installs the student-app proxy and starts the deploy worker.
set -euo pipefail
APP=/srv/aidolab/app

grep -q '^HOSTING_BACKEND=aws' /etc/aidolab/env || { echo "HOSTING_BACKEND=aws /etc/aidolab/env-д алга (terraform output gateway_env)."; exit 1; }

apt-get install -y python3-certbot-dns-route53
if [ ! -d /etc/letsencrypt/live/aidolab.mn-wildcard ]; then
  certbot certonly --dns-route53 --cert-name aidolab.mn-wildcard -d '*.aidolab.mn' \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --deploy-hook 'systemctl reload nginx'
fi

install -m 644 "$APP/deploy/nginx-apps.conf" /etc/nginx/sites-available/aidolab-apps
ln -sf /etc/nginx/sites-available/aidolab-apps /etc/nginx/sites-enabled/aidolab-apps
nginx -t && systemctl reload nginx

install -m 644 "$APP/deploy/aidolab-worker.service" /etc/systemd/system/aidolab-worker.service
systemctl daemon-reload
systemctl enable --now aidolab-worker
echo "Hosting идэвхжлээ. Worker: journalctl -u aidolab-worker -f"
