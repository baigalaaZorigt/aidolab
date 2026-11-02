#!/usr/bin/env bash
# Second step on the gateway built by infra/ (after bootstrap.sh): turns on real hosting.
#   1. Append `terraform output -raw gateway_env` to /etc/aidolab/env
#   2. sudo bash /srv/aidolab/app/deploy/bootstrap-hosting.sh
# Gets the *.aidolab.mn wildcard certificate (DNS-01 via Route 53, using the instance role),
# installs the student-app proxy, starts the deploy worker and the custom-domain units.
# Safe to re-run after `update.sh` to pick up changed nginx/systemd files.
set -euo pipefail
APP=/srv/aidolab/app

grep -q '^HOSTING_BACKEND=aws' /etc/aidolab/env || { echo "HOSTING_BACKEND=aws /etc/aidolab/env-д алга (terraform output gateway_env)."; exit 1; }

apt-get install -y python3-certbot-dns-route53
# pg_dump/pg_restore for the pre-migration database backups (checker/aws.py). Must match the RDS
# major version (infra/data.tf), so they come from the PostgreSQL project's apt repo, not Ubuntu's.
if [ ! -x /usr/lib/postgresql/17/bin/pg_dump ]; then
  install -d /usr/share/postgresql-common/pgdg
  curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update
  apt-get install -y postgresql-client-17
fi
grep -q '^AIDOLAB_BACKUP_BUCKET=' /etc/aidolab/env || echo "!! AIDOLAB_BACKUP_BUCKET /etc/aidolab/env-д алга (terraform output gateway_env). Migration-тэй deploy зогсоно."
if [ ! -d /etc/letsencrypt/live/aidolab.mn-wildcard ]; then
  certbot certonly --dns-route53 --cert-name aidolab.mn-wildcard -d '*.aidolab.mn' \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --deploy-hook 'systemctl reload nginx'
fi

# Company landing page on aidolab.mn + www (DNS-01, so it works before the A record exists).
if [ ! -d /etc/letsencrypt/live/aidolab.mn-site ]; then
  certbot certonly --dns-route53 --cert-name aidolab.mn-site -d aidolab.mn -d www.aidolab.mn \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --deploy-hook 'systemctl reload nginx'
fi
install -m 644 "$APP/deploy/nginx-site.conf" /etc/nginx/sites-available/aidolab-site
ln -sf /etc/nginx/sites-available/aidolab-site /etc/nginx/sites-enabled/aidolab-site

install -m 644 "$APP/deploy/nginx-app-proxy.conf" /etc/nginx/snippets/aidolab-app-proxy.conf
install -d -m 755 /etc/nginx/aidolab-domains /var/www/letsencrypt
install -d -o aidolab -g aidolab -m 755 /srv/aidolab/data/domains
install -m 644 "$APP/deploy/nginx-apps.conf" /etc/nginx/sites-available/aidolab-apps
ln -sf /etc/nginx/sites-available/aidolab-apps /etc/nginx/sites-enabled/aidolab-apps
nginx -t && systemctl reload nginx

install -m 644 "$APP/deploy/aidolab-worker.service" /etc/systemd/system/aidolab-worker.service
# Custom domains: the worker writes the list, this root unit issues certificates (sync-domains.py).
install -D -m 755 "$APP/deploy/sync-domains.py" /usr/local/lib/aidolab/sync-domains.py
install -m 644 "$APP"/deploy/aidolab-domains.{service,path,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aidolab-worker aidolab-domains.path aidolab-domains.timer
echo "Hosting идэвхжлээ. Worker: journalctl -u aidolab-worker -f"
