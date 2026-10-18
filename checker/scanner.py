"""Clone a public GitHub repo and run static security / deploy-readiness checks.

Nothing from the repo is ever executed: we only read files, run bandit (static
analysis) and look up pinned dependency versions in the OSV database.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

GITHUB_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+?(\.git)?/?$")
MAX_REPO_MB = 50
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages"}

SEVERITY_PENALTY = {"high": 15, "medium": 7, "low": 3}
# Generic "password = '...'" matches are only meaningful in code/config, not in docs.
CODE_SUFFIXES = {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh"}


@dataclass
class Finding:
    id: str
    severity: str  # high | medium | low
    title: str
    file: str = ""
    line: int = 0
    detail: str = ""


class ScanError(Exception):
    pass


# --- Secrets -----------------------------------------------------------------

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-(proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Database URL with password", re.compile(r"(postgres(ql)?|mysql|mongodb(\+srv)?)://[^:\s/]+:[^@\s]{3,}@")),
]
GENERIC_SECRET_RE = re.compile(
    r"""(?i)\b(password|passwd|secret|secret_key|api_key|apikey|token|access_key)\s*[:=]\s*['"]([^'"\s]{8,})['"]"""
)
PLACEHOLDER_RE = re.compile(r"(?i)(your|example|changeme|xxx|placeholder|dummy|test|<|\{)")


def mask(value: str) -> str:
    return value[:4] + "…" + "*" * 6 if len(value) > 6 else "***"


def iter_text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield path, text


def check_secrets(root: Path) -> list[Finding]:
    findings = []
    for path, text in iter_text_files(root):
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    findings.append(Finding("secret", "high", f"{label} кодонд ил байна", rel, lineno, mask(m.group(0))))
                    break
            else:
                if path.suffix not in CODE_SUFFIXES and not path.name.startswith(".env"):
                    continue
                m = GENERIC_SECRET_RE.search(line)
                if m and not PLACEHOLDER_RE.search(m.group(2)) and "environ" not in line and "getenv" not in line:
                    findings.append(Finding("secret", "high", f"'{m.group(1)}' утга кодонд шууд бичигдсэн", rel, lineno, mask(m.group(2))))
    return findings


# --- Files & config ------------------------------------------------------------

def check_repo_files(root: Path) -> list[Finding]:
    findings = []
    for path in root.rglob(".env*"):
        if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        if path.name in (".env.example", ".env.sample", ".env.template"):
            continue
        findings.append(Finding("env_committed", "high", ".env файл GitHub-д орсон байна", str(path.relative_to(root))))

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        findings.append(Finding("no_gitignore", "medium", ".gitignore файл байхгүй"))
    elif ".env" not in gitignore.read_text(errors="ignore"):
        findings.append(Finding("gitignore_env", "medium", ".gitignore дотор .env бүртгэгдээгүй", ".gitignore"))

    for path, text in iter_text_files(root):
        if path.suffix != ".py":
            continue
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if re.match(r"^DEBUG\s*=\s*True\b", stripped):
                findings.append(Finding("django_debug", "high", "DEBUG = True шууд бичигдсэн", rel, lineno))
            if re.match(r"^ALLOWED_HOSTS\s*=\s*\[\s*['\"]\*['\"]\s*\]", stripped):
                findings.append(Finding("allowed_hosts", "medium", "ALLOWED_HOSTS = ['*'] — бүх домэйнд нээлттэй", rel, lineno))
            m = re.match(r"^SECRET_KEY\s*=\s*['\"]([^'\"]*)['\"]", stripped)
            if m and len(m.group(1)) >= 8:
                findings.append(Finding("hardcoded_secret_key", "high", "SECRET_KEY кодонд шууд бичигдсэн", rel, lineno))
    return findings


# --- Hosting readiness -----------------------------------------------------------
# Apps run in containers that are replaced on every deploy, so the local disk is not kept.

FILE_WRITE_RE = re.compile(r"""\bopen\([^)]*,\s*['"](w|a|wb|ab|w\+)['"]|\.save\(\s*os\.path\.join\(|MEDIA_ROOT\s*=""")
SQLITE_RE = re.compile(r"django\.db\.backends\.sqlite3|sqlite3\.connect\(|sqlite:///")


def has_start_command(root: Path) -> bool:
    procfile = root / "Procfile"
    if procfile.exists() and re.search(r"(?m)^web:", procfile.read_text(errors="ignore")):
        return True
    if (root / "Dockerfile").exists() or (root / "app.py").exists():
        return True
    reqs = (root / "requirements.txt").read_text(errors="ignore").lower() if (root / "requirements.txt").exists() else ""
    if (root / "main.py").exists() and "fastapi" in reqs:
        return True
    return any(p for p in root.rglob("wsgi.py") if not any(part in SKIP_DIRS for part in p.parts))


def check_hosting(root: Path) -> list[Finding]:
    findings = []
    if not has_start_command(root):
        findings.append(Finding("no_start_command", "medium", "Апп-ыг эхлүүлэх команд олдсонгүй",
                                "Procfile", 0, "Procfile файлд жишээ нь: web: gunicorn app:app --bind 0.0.0.0:$PORT"))
    uses_database_url = False
    sqlite_hits, write_hits = [], []
    for path, text in iter_text_files(root):
        if path.suffix != ".py":
            continue
        rel = str(path.relative_to(root))
        uses_database_url = uses_database_url or "DATABASE_URL" in text
        for lineno, line in enumerate(text.splitlines(), 1):
            if SQLITE_RE.search(line):
                sqlite_hits.append((rel, lineno))
            if FILE_WRITE_RE.search(line):
                write_hits.append((rel, lineno))
    if sqlite_hits and not uses_database_url:
        rel, lineno = sqlite_hits[0]
        findings.append(Finding("sqlite_in_prod", "low", "SQLite ашиглаж байна — deploy бүрт өгөгдөл устна", rel, lineno,
                                "Postgres өгөгдлийн сан үүсгээд DATABASE_URL орчны хувьсагчаас уншина уу."))
    for rel, lineno in write_hits[:2]:
        findings.append(Finding("local_file_write", "low", "Файлыг серверийн дискэнд хадгалж байна — deploy бүрт устна",
                                rel, lineno, "Файлын санг (S3) ашиглана уу: S3_BUCKET, S3_PREFIX орчны хувьсагч."))
    return findings


def parse_requirements(root: Path) -> tuple[list[tuple[str, str]], list[str], Path | None]:
    req = root / "requirements.txt"
    if not req.exists():
        found = [p for p in root.rglob("requirements.txt") if not any(s in p.parts for s in SKIP_DIRS)]
        req = found[0] if found else None
    if req is None:
        return [], [], None
    pinned, unpinned = [], []
    for raw in req.read_text(errors="ignore").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*==\s*([\w.\-+]+)", line)
        if m:
            pinned.append((re.sub(r"\[.*\]", "", m.group(1)), m.group(2)))
        else:
            unpinned.append(re.split(r"[<>=!~ ;]", line)[0])
    return pinned, unpinned, req


def check_dependencies(root: Path) -> list[Finding]:
    pinned, unpinned, req = parse_requirements(root)
    has_python = any(root.rglob("*.py"))
    if req is None:
        if has_python:
            return [Finding("no_requirements", "medium", "requirements.txt байхгүй — сервер сангуудаа суулгаж чадахгүй")]
        return []
    rel = str(req.relative_to(root))
    findings = []
    if unpinned:
        findings.append(Finding("unpinned", "low", "Сангийн хувилбар тогтоогоогүй (==)", rel,
                                detail=", ".join(unpinned[:10])))
    names = {n.lower() for n, _ in pinned} | {n.lower() for n in unpinned}
    web = names & {"django", "flask", "fastapi"}
    if web and not names & {"gunicorn", "uvicorn", "waitress"}:
        findings.append(Finding("no_wsgi", "medium", "Production сервер (gunicorn/uvicorn) requirements-д алга", rel))
    findings += query_osv(pinned, rel)
    return findings


def query_osv(pinned: list[tuple[str, str]], rel: str) -> list[Finding]:
    if not pinned:
        return []
    body = json.dumps({"queries": [
        {"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pinned
    ]}).encode()
    req = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            results = json.load(resp)["results"]
    except Exception:
        return [Finding("osv_unavailable", "low", "Сангийн эмзэг байдлын санд холбогдож чадсангүй", rel)]
    findings = []
    for (name, version), result in zip(pinned, results):
        vulns = result.get("vulns") or []
        if vulns:
            ids = ", ".join(v["id"] for v in vulns[:5])
            findings.append(Finding("vulnerable_dep", "high" if len(vulns) >= 3 else "medium",
                                    f"{name}=={version} хувилбарт {len(vulns)} мэдэгдэж буй эмзэг байдал бий",
                                    rel, detail=ids))
    return findings


# --- Bandit --------------------------------------------------------------------

def check_bandit(root: Path) -> list[Finding]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", str(root), "-f", "json", "-q",
             "-x", ",".join(str(root / d) for d in SKIP_DIRS), "--severity-level", "medium"],
            capture_output=True, text=True, timeout=90,
        )
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    findings = []
    for r in data.get("results", []):
        findings.append(Finding(
            f"bandit_{r['test_id']}", r["issue_severity"].lower(), r["issue_text"],
            str(Path(r["filename"]).relative_to(root)), r["line_number"], r["test_id"],
        ))
    return findings


# --- Entry point -----------------------------------------------------------------

def git_env(token: str | None = None) -> dict:
    """Env for git. A GitHub App token goes in an env-only header: never in the URL, argv or .git/config."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {auth}")
    return env


def clone(url: str, dest: Path, token: str | None = None) -> None:
    if not GITHUB_URL_RE.match(url):
        raise ScanError("GitHub repo-гийн линк оруулна уу: https://github.com/нэр/repo")
    env = git_env(token)
    try:
        subprocess.run(
            ["git", "-c", "core.symlinks=false", "clone", "--depth", "1", "--single-branch",
             "--no-tags", url, str(dest)],
            check=True, capture_output=True, timeout=60, env=env,
        )
    except subprocess.TimeoutExpired:
        raise ScanError("Repo татахад хэт удаж байна.")
    except subprocess.CalledProcessError:
        raise ScanError("Repo-г татаж чадсангүй. Линк зөв эсэхийг шалгаад, дээрх \"Repo нэмэх\" товчоор "
                        "энэ repo-г aidolab-д харуулахыг зөвшөөрнө үү.")
    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1_000_000
    if size_mb > MAX_REPO_MB:
        raise ScanError(f"Repo хэт том байна ({size_mb:.0f}MB > {MAX_REPO_MB}MB).")


def remote_head(url: str, token: str | None = None) -> str | None:
    """Commit at the tip of the repo's default branch, without downloading it."""
    if not GITHUB_URL_RE.match(url):
        return None
    env = git_env(token)
    try:
        out = subprocess.run(["git", "ls-remote", url, "HEAD"], check=True, capture_output=True,
                             text=True, timeout=20, env=env).stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    sha = out.split("\t", 1)[0].strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def score(findings: list[Finding]) -> int:
    # Repeats of the same problem count at most twice, so one habit doesn't zero the score.
    counts: dict[str, int] = {}
    penalty = 0
    for f in findings:
        counts[f.id] = counts.get(f.id, 0) + 1
        if counts[f.id] <= 2:
            penalty += SEVERITY_PENALTY.get(f.severity, 0)
    return max(0, 100 - penalty)


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen, unique = set(), []
    for f in findings:
        key = (f.file, f.line) if f.line else (f.id, f.file, f.title)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def scan_repo(url: str, token: str | None = None) -> dict:
    url = url.strip()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        clone(url, root, token)
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        findings = (check_secrets(root) + check_repo_files(root) + check_hosting(root)
                    + check_dependencies(root) + check_bandit(root))
    order = {"high": 0, "medium": 1, "low": 2}
    findings = dedupe(findings)
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.file, f.line))
    return {"url": url, "score": score(findings), "findings": [asdict(f) for f in findings],
            "commit": commit}
