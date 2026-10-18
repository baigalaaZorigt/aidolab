"""Private repos through the aidolab GitHub App (read-only Contents + Metadata).

The student installs the app on their own GitHub account and picks the repos to share.
GitHub sends them back to /github/installed/ with an installation id; we check that the
installation belongs to the GitHub account linked to the user, then store only the id.
For each scan/build/push check we mint a 1-hour installation token.

Env: GITHUB_APP_ID, GITHUB_APP_SLUG, GITHUB_APP_PRIVATE_KEY (base64 of the .pem file, so the
env file stays one word per value for update.sh; a raw PEM with "\\n" escapes also works).
Missing env = feature hidden, public repos work as before.
"""
import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

log = logging.getLogger(__name__)
API = "https://api.github.com"
TIMEOUT = 10
_tokens: dict[int, tuple[str, float]] = {}  # installation id -> (token, expires at)


class GitHubAppError(Exception):
    pass


def enabled() -> bool:
    return all(os.environ.get(k) for k in ("GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_APP_PRIVATE_KEY"))


def install_url() -> str:
    return f"https://github.com/apps/{os.environ['GITHUB_APP_SLUG']}/installations/new"


def _app_jwt() -> str:
    now = int(time.time())
    key = os.environ["GITHUB_APP_PRIVATE_KEY"]
    key = key.replace("\\n", "\n") if key.startswith("-----") else base64.b64decode(key).decode()
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": os.environ["GITHUB_APP_ID"]}, key, algorithm="RS256")


def _api(path: str, token: str, method: str = "GET") -> dict:
    req = urllib.request.Request(API + path, method=method, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "aidolab", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise GitHubAppError(f"GitHub {e.code}") from e
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        raise GitHubAppError("GitHub хариу өгсөнгүй") from e


def installation(installation_id: int) -> dict:
    return _api(f"/app/installations/{installation_id}", _app_jwt())


def token(installation_id: int) -> str:
    cached = _tokens.get(installation_id)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    data = _api(f"/app/installations/{installation_id}/access_tokens", _app_jwt(), method="POST")
    _tokens[installation_id] = (data["token"], time.time() + 3000)  # GitHub gives 60 min
    return data["token"]


def repo_owner(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").split("github.com/")[-1].split("/")[0].lower()


def repo_token(user, url: str) -> str | None:
    """Token that can read `url` if the user installed the app on the repo owner's account."""
    if not enabled():
        return None
    inst = user.github_installations.filter(account_login__iexact=repo_owner(url)).first()
    if inst is None:
        return None
    try:
        return token(inst.installation_id)
    except GitHubAppError:
        log.exception("installation token for %s failed", inst.installation_id)
        return None


def installed_repos(user) -> list[dict]:
    """All repos (public or private) the user shared with the app, in the same shape as oauth.github_repos."""
    if not enabled():
        return []
    repos = []
    for inst in user.github_installations.all():
        try:
            data = _api("/installation/repositories?per_page=100", token(inst.installation_id))
        except GitHubAppError:
            log.exception("listing repos of installation %s failed", inst.installation_id)
            continue
        repos += [{"name": r["full_name"], "url": r["html_url"], "description": r.get("description") or "",
                   "language": r.get("language") or "", "pushed": (r.get("pushed_at") or "")[:10]}
                  for r in data.get("repositories", []) if not r.get("archived")]
    return repos
