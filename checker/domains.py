"""Custom domains: ownership (TXT) check, routing check and the hand-off to root for certificates.

Demo mode: a verified TXT record is enough ("SSL active" is simulated).
Real hosting: the worker calls sync() every minute. It
  1. re-checks pending TXT records (every 5 min for 2 h, then every 30 min, failed after 48 h),
  2. checks that verified domains point at the gateway (A record, CNAME is followed),
  3. writes "<domain> <slug>" lines to DOMAINS_DIR/wanted,
  4. reads DOMAINS_STATUS, written by deploy/sync-domains.py (root), and stores the SSL state.
The worker never runs certbot or touches nginx itself; root only trusts the validated wanted file.
"""
import json
import os
import tempfile

from django.conf import settings
from django.utils import timezone

from .hosting import is_aws
from .models import CustomDomain
from .naming import dns_lookup

SSL_PENDING, SSL_ACTIVE, SSL_ERROR = "pending", "active", "error"


def verify(domain: CustomDomain) -> None:
    records = dns_lookup(domain.txt_name, "TXT")
    if records is not None:
        if domain.token in records:
            domain.status = CustomDomain.VERIFIED
            if not domain.ssl_status:
                domain.ssl_status = SSL_PENDING if is_aws() else SSL_ACTIVE
        elif timezone.now() - domain.created_at > timezone.timedelta(hours=48):
            domain.status = CustomDomain.FAILED
    domain.last_checked_at = timezone.now()
    domain.save()


def points_here(domain: CustomDomain) -> bool:
    return settings.GATEWAY_IP in (dns_lookup(domain.domain, "A") or [])


def recheck_due(domain: CustomDomain, now) -> bool:
    if domain.last_checked_at is None:
        return True
    young = now - domain.created_at < timezone.timedelta(hours=2)
    return now - domain.last_checked_at > timezone.timedelta(minutes=5 if young else 30)


def write_wanted(lines: list[str]) -> None:
    folder = settings.DOMAINS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "wanted"
    content = "".join(f"{line}\n" for line in sorted(lines))
    if path.exists() and path.read_text() == content:
        return
    # Atomic replace: the root path unit wakes on the rename and never sees half a file.
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".wanted-")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_status() -> dict:
    try:
        return json.loads(settings.DOMAINS_STATUS.read_text())
    except (OSError, ValueError):
        return {}


def sync() -> None:
    now = timezone.now()
    for d in CustomDomain.objects.filter(status=CustomDomain.PENDING):
        if recheck_due(d, now):
            verify(d)

    status = read_status()
    wanted = []
    for d in CustomDomain.objects.filter(status=CustomDomain.VERIFIED).select_related("service"):
        report = status.get(d.domain)
        if report is not None:
            ssl_status = SSL_ACTIVE if report.get("ok") else SSL_ERROR
            ssl_error = "" if report.get("ok") else str(report.get("error", ""))[:300]
            if (d.ssl_status, d.ssl_error) != (ssl_status, ssl_error):
                d.ssl_status, d.ssl_error = ssl_status, ssl_error
                d.save(update_fields=["ssl_status", "ssl_error"])
        # Only hand a domain to certbot once it reaches us, so failed HTTP checks don't burn rate limits.
        # Active domains stay listed so a short DNS hiccup never removes a working certificate.
        if d.ssl_status == SSL_ACTIVE or points_here(d):
            wanted.append(f"{d.domain} {d.service.slug}")
    write_wanted(wanted)
