"""Validation for app subdomains (<slug>.aidolab.mn) and custom domains, plus DNS checks."""
import json
import re
import urllib.parse
import urllib.request

from .models import PLATFORM_DOMAIN

RESERVED = {
    "www", "api", "admin", "app", "deploy", "mail", "smtp", "ftp", "ns", "ns1", "ns2",
    "status", "static", "media", "docs", "help", "support", "billing", "dashboard", "login",
}
# Lookalike names used for phishing: banks, payment apps, government.
BLOCKED_SUBSTRINGS = (
    "khanbank", "golomt", "xacbank", "statebank", "capitron", "bogdbank", "arigbank", "tdbm",
    "qpay", "socialpay", "monpay", "emongolia", "e-mongolia", "police", "tatvar",
)
BLOCKED_TOKENS = {"gov", "tdb", "bank", "login", "verify"}

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def slug_error(slug: str) -> str | None:
    """Return a Mongolian error message, or None if the slug is allowed."""
    if not SLUG_RE.match(slug) or "--" in slug:
        return "Нэр 3–40 тэмдэгттэй, зөвхөн жижиг латин үсэг, тоо, зураас (-) агуулна. Зураасаар эхэлж, төгсөхгүй."
    if slug in RESERVED:
        return "Энэ нэр системд нөөцлөгдсөн байна. Өөр нэр сонгоно уу."
    if any(b in slug for b in BLOCKED_SUBSTRINGS) or BLOCKED_TOKENS & set(slug.split("-")):
        return "Банк, төлбөрийн үйлчилгээ, төрийн байгууллагатай андуурагдах нэр ашиглах боломжгүй."
    return None


def slug_from_repo(repo_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", repo_name.split("/")[-1].lower()).strip("-")[:40].strip("-")
    return base if len(base) >= 3 else f"app-{base}".strip("-")


def normalize_domain(raw: str) -> str:
    domain = raw.strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0].rstrip(".")
    return domain


def domain_error(domain: str) -> str | None:
    if not DOMAIN_RE.match(domain):
        return "Домэйн буруу байна. Жишээ: www.minii-delguur.mn"
    if domain == PLATFORM_DOMAIN or domain.endswith("." + PLATFORM_DOMAIN):
        return f"{PLATFORM_DOMAIN} домэйн аль хэдийн автоматаар холбогдсон байна."
    return None


def dns_lookup(name: str, rtype: str) -> list[str] | None:
    """Resolve via DNS-over-HTTPS (domain names are public data). None on network error."""
    url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": name, "type": rtype})
    types = {"A": 1, "CNAME": 5, "TXT": 16}
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    return [a["data"].strip('"').rstrip(".").lower()
            for a in data.get("Answer", []) if a.get("type") == types[rtype]]
