"""Log in with GitHub or Google (OAuth 2), and list a user's public GitHub repos.

Uses only urllib, like naming.py. Client ids/secrets come from env vars:
GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET, GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
A provider whose env vars are missing is simply hidden.
"""
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.urls import reverse

from .accounts import safe_next
from .audit import audit
from .models import SocialAccount

PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        # Read-only profile + email. No "repo" scope: we never get access to private code.
        "scope": "read:user user:email",
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
    },
}
TIMEOUT = 10


class OAuthError(Exception):
    pass


def enabled(provider: str) -> bool:
    key = provider.upper()
    return bool(os.environ.get(f"{key}_CLIENT_ID") and os.environ.get(f"{key}_CLIENT_SECRET"))


def enabled_providers() -> dict:
    return {p: enabled(p) for p in PROVIDERS}


def _http(url: str, data: dict | None = None, token: str = "") -> dict | list:
    headers = {"Accept": "application/json", "User-Agent": "aidolab"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        raise OAuthError(f"{url.split('/')[2]} хариу өгсөнгүй") from e


def _callback_url(request, provider: str) -> str:
    return request.build_absolute_uri(reverse("oauth_callback", args=[provider]))


def start(request, provider):
    if provider not in PROVIDERS or not enabled(provider):
        messages.error(request, "Энэ нэвтрэх арга одоогоор идэвхгүй байна.")
        return redirect("login")
    state = secrets.token_urlsafe(24)
    request.session["oauth"] = {"state": state, "provider": provider, "next": safe_next(request)}
    params = {
        "client_id": os.environ[f"{provider.upper()}_CLIENT_ID"],
        "redirect_uri": _callback_url(request, provider),
        "scope": PROVIDERS[provider]["scope"],
        "state": state,
    }
    if provider == "google":
        params.update(response_type="code", prompt="select_account")
    else:
        params["allow_signup"] = "true"
    return redirect(PROVIDERS[provider]["authorize"] + "?" + urllib.parse.urlencode(params))


def _profile(request, provider: str, code: str) -> dict:
    """Exchange the code for a token and return uid, email, name, login, token."""
    cfg = PROVIDERS[provider]
    tok = _http(cfg["token"], {
        "client_id": os.environ[f"{provider.upper()}_CLIENT_ID"],
        "client_secret": os.environ[f"{provider.upper()}_CLIENT_SECRET"],
        "code": code,
        "redirect_uri": _callback_url(request, provider),
        "grant_type": "authorization_code",
    })
    token = tok.get("access_token") if isinstance(tok, dict) else None
    if not token:
        raise OAuthError("Нэвтрэх зөвшөөрөл авч чадсангүй")
    if provider == "github":
        user = _http("https://api.github.com/user", token=token)
        emails = _http("https://api.github.com/user/emails", token=token)
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        return {"uid": str(user["id"]), "login": user.get("login", ""), "name": user.get("name") or user.get("login", ""),
                "email": (primary or {}).get("email", "").lower(), "token": token}
    info = _http("https://openidconnect.googleapis.com/v1/userinfo", token=token)
    email = info.get("email", "").lower() if info.get("email_verified") else ""
    return {"uid": str(info["sub"]), "login": email, "name": info.get("given_name") or info.get("name", ""),
            "email": email, "token": ""}  # Google token is not needed after login


def _link(user, provider: str, profile: dict) -> SocialAccount:
    account, _ = SocialAccount.objects.update_or_create(
        provider=provider, uid=profile["uid"],
        defaults={"user": user, "login": profile["login"], "token": profile["token"]},
    )
    return account


def _log_in(request, user, next_url: str, note: str):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    audit(request, "auth", note)
    return redirect(next_url)


def callback(request, provider):
    saved = request.session.pop("oauth", None)
    if (not saved or provider not in PROVIDERS or saved["provider"] != provider
            or not secrets.compare_digest(saved["state"], request.GET.get("state", ""))):
        messages.error(request, "Нэвтрэх хүсэлт хүчингүй боллоо. Дахин оролдоно уу.")
        return redirect("login")
    label = SocialAccount.PROVIDERS[provider]
    if "code" not in request.GET:  # user pressed "Cancel" on GitHub/Google
        messages.error(request, f"{label}-ээр нэвтрэхийг цуцаллаа.")
        return redirect("login")
    try:
        profile = _profile(request, provider, request.GET["code"])
    except (OAuthError, KeyError) as e:
        messages.error(request, f"{label}-ээр нэвтэрч чадсангүй: {e}")
        return redirect("login")

    existing = SocialAccount.objects.filter(provider=provider, uid=profile["uid"]).select_related("user").first()

    # Already logged in: connect this account to the current user.
    if request.user.is_authenticated:
        if existing and existing.user_id != request.user.id:
            messages.error(request, f"Энэ {label} бүртгэл өөр хэрэглэгчид холбогдсон байна.")
        else:
            _link(request.user, provider, profile)
            audit(request, "auth", f"{label} бүртгэл холбов")
            messages.success(request, f"{label} холбогдлоо.")
        return redirect(saved["next"])

    if existing:
        _link(existing.user, provider, profile)  # refresh token / username
        return _log_in(request, existing.user, saved["next"], f"{label}-ээр нэвтэрлээ")
    if not profile["email"]:
        messages.error(request, f"{label} бүртгэлд баталгаажсан имэйл алга. Имэйлээрээ бүртгүүлнэ үү.")
        return redirect("signup")
    # Same verified email as an existing account: link it (the provider verified the email).
    user = User.objects.filter(username=profile["email"]).first()
    if user:
        _link(user, provider, profile)
        return _log_in(request, user, saved["next"], f"{label}-ээр нэвтэрлээ (бүртгэл холбов)")
    # New user: create the account straight away with the provider's name and verified email.
    name = (profile["name"] or profile["email"].split("@")[0])[:150]
    user = User.objects.create_user(username=profile["email"], email=profile["email"], first_name=name)
    user.set_unusable_password()
    user.save()
    _link(user, provider, profile)
    messages.success(request, f"Тавтай морил, {name}! Эхний repo-гоо шалгуулаарай.")
    # A new user goes to /new/ unless they came from a specific step (e.g. /github/connect/).
    next_url = saved["next"] if saved["next"] != "/dashboard/" else reverse("new")
    return _log_in(request, user, next_url, f"{label}-ээр бүртгэл үүсгэв")


def github_repos(user) -> list[dict] | None:
    """Public repos of the user's linked GitHub account, newest first. None if not linked."""
    account = user.social_accounts.filter(provider="github").first()
    if not account:
        return None
    url = (f"https://api.github.com/users/{urllib.parse.quote(account.login)}/repos?"
           + urllib.parse.urlencode({"sort": "pushed", "per_page": 100, "type": "owner"}))
    try:
        repos = _http(url, token=account.token)
    except OAuthError:
        # The saved token can be revoked on GitHub (401). Public repos don't need it,
        # so try once without; the next GitHub login saves a fresh token.
        try:
            repos = _http(url)
        except OAuthError:
            return []
    return [{"name": r["full_name"], "url": r["html_url"], "description": r.get("description") or "",
             "language": r.get("language") or "", "pushed": (r.get("pushed_at") or "")[:10]}
            for r in repos if isinstance(r, dict) and not r.get("private") and not r.get("archived")]
