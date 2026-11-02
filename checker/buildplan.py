"""Decide how to build and start a repo, by reading its files only (nothing is executed).

plan(root) is the single source of truth: the scanner uses it to tell whether an app can
start, and the deploy worker sends its Dockerfile to CodeBuild (infra/buildspec.yml) and
runs its release command (database migrations) before the new version goes live.
"""
import json
import re
import shlex
import tomllib
from pathlib import Path

PORT = 8000
PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13")
DEFAULT_PYTHON = "3.12"
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages", ".next", "dist", "build"}
# Root-level files tried as the Python entry point, in this order; other root .py files come after.
PY_ENTRIES = ("app.py", "main.py", "server.py", "streamlit_app.py", "application.py", "run.py", "bot.py")
NODE_SERVERS = {"express", "fastify", "koa", "hono", "@nestjs/core", "@hapi/hapi", "next", "nuxt",
                "@sveltejs/kit", "@remix-run/serve", "socket.io"}
STATIC_NGINX = ("server { listen 8000; root /usr/share/nginx/html; index index.html; "
                "location ~ /\\. { deny all; } location / { try_files $uri $uri/ %s; } }")


def read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore") if path.is_file() and not path.is_symlink() else ""
    except OSError:
        return ""


def package_json(root: Path) -> dict | None:
    try:
        data = json.loads(read(root / "package.json"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]


def uses_database_url(root: Path) -> bool:
    if "DATABASE_URL" in read(root / "prisma" / "schema.prisma"):
        return True
    return any("DATABASE_URL" in read(p) for p in py_files(root)[:500])


def procfile_web(root: Path) -> str:
    m = re.search(r"(?m)^web:[ \t]*(.+)$", read(root / "Procfile"))
    return m.group(1).strip() if m else ""


def result(kind: str, label: str, runtime: str, start: str = "", release: str = "",
           release_in_task: bool = False, dockerfile: str = "") -> dict:
    """`release` runs once per deploy: in its own task before the switch (release_in_task, the app
    uses DATABASE_URL so the data must survive) or else in front of the start command."""
    if release and not release_in_task and start:
        start = f"{release} && {start}"
    return {"kind": kind, "label": label, "runtime": runtime, "start": start,
            "release": release if release_in_task else "", "dockerfile": dockerfile}


def plan(root: Path) -> dict:
    p = detect(root)
    if (root / "Dockerfile").is_file():  # the repo's own Dockerfile wins; its CMD starts the app
        p.update(kind="dockerfile", label="Dockerfile", dockerfile="", start=p["start"] or "Dockerfile")
    return p


def detect(root: Path) -> dict:
    pkg = package_json(root)
    has_py = bool(py_files(root)) or (root / "requirements.txt").exists()
    if pkg is not None and not has_py:
        return node_plan(root, pkg)
    if has_py:
        return python_plan(root)
    for folder in ("", "public", "docs", "site"):
        if (root / folder / "index.html").is_file():
            return static_plan(folder)
    return result("", "", "static")


# --- Python ----------------------------------------------------------------------------

def python_version(root: Path) -> str:
    text = read(root / ".python-version") or read(root / "runtime.txt")
    m = re.search(r"3\.\d+", text)
    return m.group(0) if m and m.group(0) in PYTHON_VERSIONS else DEFAULT_PYTHON


def pyproject_deps(root: Path) -> list[str]:
    try:
        deps = tomllib.loads(read(root / "pyproject.toml")).get("project", {}).get("dependencies", [])
    except tomllib.TOMLDecodeError:
        return []
    return [d for d in deps if isinstance(d, str)]


def python_install(root: Path) -> str:
    if (root / "requirements.txt").exists():
        return "pip install -r requirements.txt"
    deps = pyproject_deps(root)
    return "pip install " + " ".join(shlex.quote(d) for d in deps) if deps else ""


def find_entry(root: Path) -> tuple[str, str, str]:
    """(framework, file, app variable) for the first root-level file that builds a web app."""
    names = [n for n in PY_ENTRIES if (root / n).is_file()]
    names += sorted(p.name for p in root.glob("*.py") if p.name not in names)
    for name in names:
        text = read(root / name)
        if re.search(r"(?m)^\s*(import|from)\s+streamlit\b", text):
            return "streamlit", name, ""
        if re.search(r"(?m)^\s*(import|from)\s+gradio\b", text):
            return "gradio", name, ""
        if m := re.search(r"(?m)^(\w+)\s*=\s*FastAPI\(", text):
            return "fastapi", name, m.group(1)
        if m := re.search(r"(?m)^(\w+)\s*=\s*Flask\(", text):
            return "flask", name, m.group(1)
        if re.search(r"(?m)^def create_app\(", text) and "Flask" in text:
            return "flask", name, "create_app()"
    return "", "", ""


def django_wsgi(root: Path) -> str:
    if not (root / "manage.py").is_file():
        return ""
    found = sorted((p for p in root.rglob("wsgi.py") if not any(s in SKIP_DIRS for s in p.relative_to(root).parts)),
                   key=lambda p: len(p.parts))
    return ".".join(found[0].relative_to(root).with_suffix("").parts) if found else ""


def python_release(root: Path, framework: str, module: str) -> str:
    if (root / "manage.py").is_file():
        return "python manage.py migrate --noinput"
    if (root / "alembic.ini").is_file():
        return "alembic upgrade head"
    if framework == "flask" and (root / "migrations" / "alembic.ini").is_file():
        return f"flask --app {shlex.quote(module)} db upgrade"
    return ""


def python_plan(root: Path) -> dict:
    version = python_version(root)
    framework, entry, var = find_entry(root)
    module = entry.removesuffix(".py")
    env = {}
    if web := procfile_web(root):
        kind, label, start = "procfile", "Procfile", web
    elif wsgi := django_wsgi(root):
        kind, label, start = "django", "Django", f"gunicorn {wsgi}:application --bind 0.0.0.0:$PORT --workers 2"
    elif framework == "streamlit":
        kind, label = "streamlit", "Streamlit"
        start = (f"streamlit run {shlex.quote(entry)} --server.port $PORT --server.address 0.0.0.0 "
                 "--server.headless true --browser.gatherUsageStats false")
    elif framework == "gradio":
        kind, label, start = "gradio", "Gradio", f"python {shlex.quote(entry)}"
        env = {"GRADIO_SERVER_NAME": "0.0.0.0", "GRADIO_SERVER_PORT": str(PORT)}
    elif framework == "fastapi":
        kind, label, start = "fastapi", "FastAPI", f"uvicorn {shlex.quote(f'{module}:{var}')} --host 0.0.0.0 --port $PORT"
    elif framework == "flask":
        kind, label = "flask", "Flask"
        start = f"gunicorn {shlex.quote(f'{module}:{var}')} --bind 0.0.0.0:$PORT --workers 2"
    else:
        return result("", "", "python", release=python_release(root, "", ""))
    label = f"{label} · Python {version}"
    release = python_release(root, framework, module)
    in_task = bool(release) and uses_database_url(root)
    install = python_install(root)
    servers = {"django": "gunicorn", "flask": "gunicorn", "fastapi": "uvicorn"}.get(kind, "gunicorn uvicorn")
    run = f"{install} && pip install {servers}" if install else f"pip install {servers}"
    plan_ = result(kind, label, "python", start, release, in_task)
    env_line = "".join(f" {k}={v}" for k, v in env.items())
    plan_["dockerfile"] = "\n".join([
        f"FROM public.ecr.aws/docker/library/python:{version}-slim",
        f"ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT={PORT}{env_line}",
        "WORKDIR /app",
        "COPY . .",
        f"RUN {run}",
        "RUN useradd --create-home app && chown -R app /app",
        "USER app",
        f"EXPOSE {PORT}",
        f"CMD [\"sh\", \"-c\", {json.dumps(plan_['start'])}]",
        "",
    ])
    return plan_


# --- Node.js ---------------------------------------------------------------------------

def node_install(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm install --frozen-lockfile"
    if (root / "yarn.lock").exists():
        return "yarn install --frozen-lockfile"
    if (root / "package-lock.json").exists():
        return "npm ci"
    return "npm install"


def node_entry(root: Path, pkg: dict) -> str:
    main = pkg.get("main") if isinstance(pkg.get("main"), str) else ""
    for name in ([main] if main else []) + ["server.js", "index.js", "app.js", "server.mjs", "index.mjs"]:
        if (root / name).is_file() and ".." not in Path(name).parts:
            return name
    return ""


def is_spa(pkg: dict, scripts: dict, deps: dict) -> bool:
    if NODE_SERVERS & deps.keys() or not scripts.get("build"):
        return False
    start = scripts.get("start", "")
    return ("vite" in deps or "react-scripts" in deps) and (not start or start.split()[0] in ("vite", "react-scripts"))


def node_plan(root: Path, pkg: dict) -> dict:
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    deps = {**(pkg.get("devDependencies") or {}), **(pkg.get("dependencies") or {})}
    release = "npx prisma migrate deploy" if (root / "prisma" / "migrations").is_dir() else ""
    in_task = bool(release) and uses_database_url(root)
    if is_spa(pkg, scripts, deps):
        out = "build" if "react-scripts" in deps else "dist"
        plan_ = result("spa", "React/Vite (статик)", "static", "nginx")
        plan_["dockerfile"] = "\n".join([
            "FROM public.ecr.aws/docker/library/node:22-slim AS build",
            "ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
            "WORKDIR /app",
            "COPY . .",
            f"RUN corepack enable && {node_install(root)} && npm run build",
            "FROM public.ecr.aws/docker/library/nginx:1.27-alpine",
            f"COPY --from=build /app/{out} /usr/share/nginx/html",
            f"RUN printf '%s\\n' {shlex.quote(STATIC_NGINX % '/index.html')} > /etc/nginx/conf.d/default.conf",
            f"EXPOSE {PORT}",
            "",
        ])
        return plan_
    if web := procfile_web(root):
        kind, label, start = "procfile", "Procfile", web
    elif scripts.get("start"):
        kind, label, start = ("next" if "next" in deps else "node"), "Node.js", "npm start"
    elif "next" in deps:
        kind, label, start = "next", "Node.js", "npx next start"
    elif entry := node_entry(root, pkg):
        kind, label, start = "node", "Node.js", f"node {shlex.quote(entry)}"
    else:
        return result("", "", "node")
    label = "Next.js · Node 22" if kind == "next" else f"{label} · Node 22"
    return node_dockerfile(root, result(kind, label, "node", start, release, in_task))


def node_dockerfile(root: Path, plan_: dict) -> dict:
    plan_["dockerfile"] = "\n".join([
        "FROM public.ecr.aws/docker/library/node:22-slim",
        f"ENV PORT={PORT} NEXT_TELEMETRY_DISABLED=1 COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
        "RUN corepack enable && mkdir /app && chown node:node /app",
        "WORKDIR /app",
        "USER node",
        "COPY --chown=node:node . .",
        # devDependencies are needed for the build (typescript, tailwind), so NODE_ENV is set after it.
        f"RUN {node_install(root)}",
        'RUN if grep -q "\\"build\\"[[:space:]]*:" package.json; then npm run build; fi',
        "ENV NODE_ENV=production",
        f"EXPOSE {PORT}",
        f"CMD [\"sh\", \"-c\", {json.dumps(plan_['start'])}]",
        "",
    ])
    return plan_


# --- Static site -----------------------------------------------------------------------

def static_plan(folder: str) -> dict:
    plan_ = result("static", "Статик сайт (HTML)", "static", "nginx")
    plan_["dockerfile"] = "\n".join([
        "FROM public.ecr.aws/docker/library/nginx:1.27-alpine",
        f"COPY {folder or '.'} /usr/share/nginx/html",
        f"RUN printf '%s\\n' {shlex.quote(STATIC_NGINX % '$uri.html =404')} > /etc/nginx/conf.d/default.conf",
        f"EXPOSE {PORT}",
        "",
    ])
    return plan_


# --- Legacy ----------------------------------------------------------------------------
# Apps deployed before buildplan existed (Service.build_mode == "legacy") keep exactly what the old
# infra/buildspec.yml did, so a redeploy of a live customer app never changes how it starts:
# same detection order, Python 3.12, `pip install gunicorn uvicorn`, and no migrations.

def legacy_plan(root: Path) -> dict:
    p = legacy_detect(root)
    if (root / "Dockerfile").is_file():
        p.update(kind="dockerfile", label="Dockerfile", dockerfile="", start=p["start"] or "Dockerfile")
    return p


def legacy_detect(root: Path) -> dict:
    pkg = package_json(root)
    has_py = any(p for p in root.rglob("*.py") if not {"node_modules", ".git"} & set(p.relative_to(root).parts))
    if pkg is not None and not (root / "requirements.txt").exists() and not has_py:
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        deps = {**(pkg.get("devDependencies") or {}), **(pkg.get("dependencies") or {})}
        if web := procfile_web(root):
            start = web
        elif scripts.get("start"):
            start = "npm start"
        elif "next" in deps:
            start = "npx next start"
        else:
            return result("", "", "node")
        return node_dockerfile(root, result("node", "Node.js · Node 22 (хуучин дүрэм)", "node", start))
    reqs = read(root / "requirements.txt")
    wsgi = sorted((p for p in root.rglob("wsgi.py") if not {".venv", "venv"} & set(p.relative_to(root).parts)),
                  key=lambda p: len(p.parts))
    st = next((n for n in ["streamlit_app.py", "app.py", "main.py"] + sorted(p.name for p in root.glob("*.py"))
               if re.search(r"import streamlit", read(root / n))), "")
    if web := procfile_web(root):
        start = web
    elif wsgi:
        start = f"gunicorn {'.'.join(wsgi[0].relative_to(root).with_suffix('').parts)}:application --bind 0.0.0.0:$PORT --workers 2"
    elif re.search(r"(?im)^streamlit", reqs) and st:
        start = (f"streamlit run {st} --server.port $PORT --server.address 0.0.0.0 --server.headless true "
                 "--browser.gatherUsageStats false")
    elif (root / "main.py").is_file() and re.search(r"(?i)fastapi", reqs):
        start = "uvicorn main:app --host 0.0.0.0 --port $PORT"
    elif (root / "app.py").is_file():
        start = "gunicorn app:app --bind 0.0.0.0:$PORT --workers 2"
    else:
        return result("", "", "python")
    p = result("python", "Python 3.12 (хуучин дүрэм)", "python", start)
    p["dockerfile"] = "\n".join([
        "FROM public.ecr.aws/docker/library/python:3.12-slim",
        f"ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT={PORT}",
        "WORKDIR /app",
        "COPY . .",
        "RUN if [ -f requirements.txt ]; then pip install -r requirements.txt; fi && pip install gunicorn uvicorn",
        "RUN useradd --create-home app && chown -R app /app",
        "USER app",
        f"EXPOSE {PORT}",
        f"CMD [\"sh\", \"-c\", {json.dumps(start)}]",
        "",
    ])
    return p
