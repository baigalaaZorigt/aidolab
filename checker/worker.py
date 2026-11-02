"""Deploy worker (HOSTING_BACKEND=aws). Runs as its own process: `manage.py run_worker`.

One deploy at a time: plan (buildplan, from the scanned commit) → CodeBuild image → SSM params →
task role → task definition → [database backup → migration task] → ECS service → wait for rollout.
If the migration or the new version fails, the database goes back to the backup and the service
back to the previous version. A rollback deploy reuses an earlier image (and optionally its database).
Progress goes into Deploy.logs, which the Logs tab polls.
Between deploys it also syncs custom domains (checker/domains.py) once a minute and checks deployed
repos for new commits (auto-deploy).
"""
import logging
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from . import aws, buildplan, domains, github_app, hosting
from .models import AuditEvent, Deploy, Job, Service, profile_for
from .plans import get_plan
from .scanner import ScanError, checkout, remote_head
from .views import run_scan

log = logging.getLogger(__name__)
BUILD_TIMEOUT, ROLLOUT_TIMEOUT, HTTP_TIMEOUT, RELEASE_TIMEOUT = 30 * 60, 10 * 60, 3 * 60, 15 * 60
OLD_DATABASE_KEEP = timezone.timedelta(days=1)  # a database replaced by a restore is dropped after this
DOMAIN_SYNC_EVERY = 60
FREE_EXPIRY_EVERY = 600
PUSH_CHECK_EVERY = 60


class DeployFailed(Exception):
    pass


def app_env(service) -> dict[str, str]:
    values = {v.key: v.value for v in service.env_vars.all()}
    if hasattr(service, "database"):
        values["DATABASE_URL"] = service.database.url
    if hasattr(service, "bucket"):
        values["S3_BUCKET"] = aws.cfg("FILES_BUCKET")
        values["S3_PREFIX"] = service.bucket.prefix
    return values


def make_plan(d: Deploy, token: str | None) -> None:
    """Read the commit being deployed (never run it) and decide how to build and start it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        try:
            checkout(d.service.repo_url, root, token, d.commit)
        except ScanError as e:
            raise DeployFailed(str(e))
        d.plan = buildplan.legacy_plan(root) if d.service.build_mode == "legacy" else buildplan.plan(root)
    d.save(update_fields=["plan"])
    if not d.plan["start"]:
        raise DeployFailed("Апп-ыг яаж эхлүүлэхийг олсонгүй. Шалгалтын үр дүнгийн «эхлүүлэх команд» зөвлөгөөг харна уу.")
    d.log(f"==> Апп-ын төрөл: {d.plan['label']}")
    if d.plan["release"]:
        d.log(f"==> Migration: {d.plan['release']}")


def build(d: Deploy) -> None:
    d.state = Deploy.BUILDING
    d.save(update_fields=["state"])
    token = github_app.repo_token(d.service.owner, d.service.repo_url)
    make_plan(d, token)
    d.build_id, d.image = aws.start_build(d.service, d.number, token, d.commit, d.plan.get("dockerfile", ""))
    d.save(update_fields=["build_id", "image"])
    d.log("==> Image бүтээж эхэллээ (CodeBuild)")
    token, started = None, time.monotonic()
    while True:
        status, stream = aws.build_state(d.build_id)
        if stream:
            lines, token = aws.build_log(stream, token)
            for line in lines:
                d.logs.append([d.elapsed, line])
            if lines:
                d.save(update_fields=["logs"])
        if status == "SUCCEEDED":
            return
        if status != "IN_PROGRESS":
            raise DeployFailed("Image бүтээж чадсангүй. Дээрх log-оос алдааг харна уу.")
        if time.monotonic() - started > BUILD_TIMEOUT:
            raise DeployFailed("Image бүтээхэд хэт удлаа.")
        time.sleep(5)


def register(d: Deploy) -> str:
    service = d.service
    env = app_env(service)
    d.log(f"==> Орчны хувьсагч: {len(env)} ширхэг")
    secrets = aws.sync_params(service, env)
    task = aws.register_task(service, d.image, secrets, aws.ensure_task_role(service))
    d.task_definition = task
    d.save(update_fields=["task_definition"])
    return task


def go_live(d: Deploy, task: str) -> None:
    aws.run(d.service, task, aws.ensure_registry(d.service))
    d.log("==> Апп-ыг эхлүүлж байна (ECS Fargate Spot)")
    started = time.monotonic()
    while True:
        state, reason = aws.rollout_state(d.service)
        if state == "COMPLETED":
            break
        if state == "FAILED":
            raise DeployFailed(f"Апп эхэлсэнгүй: {reason}. Апп {aws.PORT} порт дээр ажиллаж байгаа эсэхийг шалгана уу.")
        if time.monotonic() - started > ROLLOUT_TIMEOUT:
            raise DeployFailed("Апп эхлэхэд хэт удлаа. Logs табаас апп-ын алдааг харна уу.")
        time.sleep(5)
    wait_http(d)


def backup(d: Deploy) -> None:
    key = f"{d.service.pk}/{d.number}-{timezone.now():%Y%m%d-%H%M%S}.dump"
    d.log("==> Өгөгдлийн санг нөөцөлж байна (migration-ий өмнө)")
    try:
        size = aws.backup_database(d.service.database, key)
    except Exception:
        log.exception("backup before deploy %s failed", d.pk)
        raise DeployFailed("Өгөгдлийн санг нөөцөлж чадсангүй тул migration хийсэнгүй. "
                           "Апп өмнөх хувилбараараа ажиллаж байна. Mentor-оос тусламж хүснэ үү.")
    d.backup_key = key
    d.save(update_fields=["backup_key"])
    d.log(f"==> Нөөц бэлэн ✓ ({max(1, size // 1024)} KB)")


def migrate(d: Deploy, task: str) -> None:
    """Run the release command in its own task. A database we host is backed up first."""
    command = d.plan.get("release")
    if not command:
        return
    if hasattr(d.service, "database"):
        backup(d)
    d.log(f"==> Migration ажиллуулж байна: {command}")
    arn = aws.start_release(d.service, task, command)
    token, started = None, time.monotonic()
    while True:
        status, code, reason = aws.release_state(arn)
        lines, token = aws.task_log(d.service, arn, token)
        for line in lines:
            d.logs.append([d.elapsed, line])
        if lines:
            d.save(update_fields=["logs"])
        if status == "STOPPED":
            break
        if time.monotonic() - started > RELEASE_TIMEOUT:
            aws.stop_release(arn)
            code, reason = None, "хэт удсан"
            break
        time.sleep(5)
    if code != 0:
        restore(d, d.backup_key)
        raise DeployFailed(f"Migration амжилтгүй ({reason or f'exit {code}'}). Дээрх log-оос алдааг харна уу. "
                           "Апп өмнөх хувилбараараа ажиллаж байна.")
    d.log("==> Migration дууслаа ✓")


def restore(d: Deploy, key: str) -> None:
    """Database back to the dump at `key`; the replaced database is kept for a day, then dropped."""
    if not key or not hasattr(d.service, "database"):
        return
    d.log("==> Өгөгдлийн санг нөөцөөс сэргээж байна…")
    old = aws.restore_database(d.service.database, key)
    Job.objects.create(kind="drop_database", payload={"name": old, "username": None},
                       run_after=timezone.now() + OLD_DATABASE_KEEP)
    d.log("==> Өгөгдлийн сан нөөцлөгдсөн үеийн байдалдаа буцлаа ✓")
    note(d.service, "database", f"Deploy #{d.number}: өгөгдлийн санг нөөцөөс сэргээв")


def revert(d: Deploy, previous: Deploy | None) -> None:
    """After a failed switch: database back to the backup, service back to the last live version."""
    try:
        restore(d, d.backup_key)
        if previous and previous.image:
            aws.run(d.service, aws.register_task(d.service, previous.image, aws.sync_params(d.service, app_env(d.service)),
                                                 aws.ensure_task_role(d.service)), aws.ensure_registry(d.service))
            d.log(f"==> Өмнөх хувилбар (Deploy #{previous.number}) руу буцаалаа")
    except Exception:
        log.exception("revert after deploy %s failed", d.pk)
        d.log("!! Өмнөх байдал руу автоматаар буцааж чадсангүй. Mentor-оос тусламж хүснэ үү.")


def start(d: Deploy) -> None:
    d.state = Deploy.STARTING
    d.save(update_fields=["state"])
    previous = d.service.deploys.filter(state=Deploy.LIVE).exclude(pk=d.pk).first()
    task = register(d)
    migrate(d, task)
    try:
        go_live(d, task)
    except DeployFailed:
        revert(d, previous)
        raise


def rollback(d: Deploy) -> None:
    """Earlier image goes live again; with restore_db the database goes back to that version's time."""
    target = d.rollback_of
    d.state = Deploy.STARTING
    d.save(update_fields=["state"])
    d.log(f"==> Deploy #{target.number}-ийн хувилбар руу буцааж байна (дахин build хийхгүй)")
    if d.restore_db:
        point = target.restore_point
        if point is None:
            raise DeployFailed("Энэ хувилбарын өгөгдлийн сангийн нөөц олдсонгүй.")
        restore(d, point.backup_key)
    go_live(d, register(d))


def answers_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500  # 404 etc. still means the app is up and listening
    except OSError:
        return False


def wait_http(d: Deploy) -> None:
    """ECS only knows the container runs; success is claimed only once the app answers like nginx will reach it."""
    url = f"http://{d.service.slug}.{aws.APP_DOMAIN}:{aws.PORT}/"
    d.log("==> Апп хариу өгөхийг шалгаж байна (HTTP)")
    started = time.monotonic()
    while not answers_http(url):
        if time.monotonic() - started > HTTP_TIMEOUT:
            raise DeployFailed(f"Апп ажиллаж байгаа ч {aws.PORT} порт дээр хариу өгөхгүй байна. "
                               "Апп 0.0.0.0 хаяг, PORT хувьсагч дээр сонсож байгаа эсэхийг шалгана уу.")
        time.sleep(5)
    d.log("==> HTTP хариу ирлээ ✓")


def process(d: Deploy) -> None:
    try:
        if d.rollback_of_id and d.image:
            rollback(d)
        else:
            build(d)
            start(d)
    except DeployFailed as e:
        fail(d, str(e))
        return
    except Exception as e:  # AWS/API errors: show a short message, keep details in the server log
        log.exception("deploy %s failed", d.pk)
        fail(d, f"Системийн алдаа ({type(e).__name__}). Mentor-оос тусламж хүснэ үү.")
        return
    d.state = Deploy.LIVE
    d.duration = d.elapsed
    d.save(update_fields=["state", "duration"])
    d.log(f"==> Таны апп ажиллаж байна 🎉 {d.service.url}")
    try:
        aws.prune_old_versions(d.service)
    except Exception:  # cleanup must never fail a live deploy; the next deploy tries again
        log.exception("prune after deploy %s failed", d.pk)


def fail(d: Deploy, message: str) -> None:
    d.state, d.error = Deploy.FAILED, message[:500]
    d.duration = d.elapsed
    d.save(update_fields=["state", "error", "duration"])
    d.log(f"!! {message}")


def claim_deploy() -> Deploy | None:
    with transaction.atomic():
        d = Deploy.objects.filter(state=Deploy.QUEUED).order_by("started_at").first()
        if d is None:
            return None
        # Re-check inside the transaction so two workers never take the same deploy.
        if Deploy.objects.filter(pk=d.pk, state=Deploy.QUEUED).update(state=Deploy.BUILDING) != 1:
            return None
    d.refresh_from_db()
    return d


def run_jobs() -> None:
    for job in Job.objects.filter(done=False, run_after__lte=timezone.now())[:5]:
        try:
            if job.kind == "teardown":
                aws.teardown(job.payload)
            elif job.kind == "delete_files":
                aws.delete_prefix(job.payload["prefix"])
            elif job.kind == "drop_database":
                aws.drop_database(job.payload["name"], job.payload.get("username"))
            job.done = True
        except Exception as e:
            job.attempts += 1
            job.error = f"{type(e).__name__}: {e}"[:2000]
            job.run_after = timezone.now() + timezone.timedelta(seconds=min(3600, 30 * 2 ** job.attempts))
            job.done = job.attempts >= 10
        job.save()


def recover() -> None:
    """A deploy left half-done by a worker restart is marked failed; the student can redeploy."""
    for d in Deploy.objects.filter(state__in=[Deploy.BUILDING, Deploy.STARTING]):
        fail(d, "Сервер дахин эхэлсэн тул deploy тасарлаа. Дахин deploy хийнэ үү.")


def sync_domains() -> None:
    try:
        domains.sync()
    except Exception:  # DNS/network trouble must not stop deploys
        log.exception("domain sync failed")


def expire_free() -> None:
    try:
        if stopped := hosting.expire_free_apps():
            log.info("stopped %d app(s) whose free period ended", stopped)
    except Exception:
        log.exception("free period check failed")


def note(service: Service, kind: str, message: str) -> None:
    AuditEvent.objects.create(user=service.owner, service=service, service_name=service.name, kind=kind, message=message)


def auto_deploy(service: Service, head: str) -> None:
    """A new commit was pushed: rescan it and deploy only if it still passes the deploy rules."""
    # Remember the commit first so a failing scan is not retried every minute.
    Service.objects.filter(pk=service.pk).update(last_commit=head)
    try:
        service = run_scan(service.owner, service.repo_url)
    except ScanError as e:
        note(service, "scan", f"Шинэ commit {head[:7]}: шалгаж чадсангүй — {e}")
        return
    error = hosting.deploy_error(service, get_plan(profile_for(service.owner).effective_plan))
    if error:
        note(service, "deploy", f"Шинэ commit {head[:7]}: auto-deploy хийгдсэнгүй — {error}")
        return
    d = hosting.start_deploy(service, trigger="auto")
    note(service, "deploy", f"Шинэ commit {d.commit[:7]}: Deploy #{d.number} автоматаар эхлэв")


def check_pushes() -> None:
    services = Service.objects.filter(auto_deploy=True, suspended=False, deploys__isnull=False).distinct()
    for service in services.select_related("owner"):
        try:
            head = remote_head(service.repo_url, github_app.repo_token(service.owner, service.repo_url))
            if head is None or head == service.last_commit or service.deploy_state == "building":
                continue
            if not service.last_commit:  # scanned before commits were tracked: start from the current tip
                Service.objects.filter(pk=service.pk).update(last_commit=head)
                continue
            auto_deploy(service, head)
        except Exception:  # one broken repo must not stop the others
            log.exception("auto-deploy check for service %s failed", service.pk)


def loop() -> None:
    recover()
    last_sync = last_expiry = last_push_check = 0.0
    while True:
        if time.monotonic() - last_push_check > PUSH_CHECK_EVERY:
            check_pushes()
            last_push_check = time.monotonic()
        if time.monotonic() - last_sync > DOMAIN_SYNC_EVERY:
            sync_domains()
            last_sync = time.monotonic()
        if time.monotonic() - last_expiry > FREE_EXPIRY_EVERY:
            expire_free()
            last_expiry = time.monotonic()
        d = claim_deploy()
        if d:
            process(d)
        else:
            run_jobs()
            time.sleep(2)
