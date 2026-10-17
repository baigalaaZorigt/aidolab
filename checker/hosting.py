"""Hosting actions used by the views.

HOSTING_BACKEND=demo: deploys, databases and buckets are records only (simulated logs/metrics).
HOSTING_BACKEND=aws: the same records drive real resources — checker/aws.py does the AWS calls,
checker/worker.py runs deploys and cleanup in the background.
"""
import math
import random
import re

from django.conf import settings
from django.utils import timezone

from .models import DEPLOY_SECONDS, AuditEvent, Bucket, Database, Deploy, Job, Profile, Service, profile_for
from .plans import FREE_DAYS, get_plan

MIN_DEPLOY_SCORE = 80


def is_aws() -> bool:
    return settings.HOSTING_BACKEND == "aws"


def deploy_logs(service: Service, number: int, trigger: str) -> list:
    env_count = service.env_vars.count()
    extras = [k for k, on in (("DATABASE_URL", hasattr(service, "database")),
                              ("S3_BUCKET, S3_PREFIX", hasattr(service, "bucket"))) if on]
    env_line = f"==> Орчны хувьсагч: {env_count} ширхэг" + (f" + {', '.join(extras)}" if extras else "")
    title = "Өмнөх хувилбар руу буцааж байна" if trigger == "rollback" else "Deploy эхэллээ"
    return [
        [0, f"==> #{number} {title} · {service.repo_url} (main)"],
        [1, "==> Repo-г татаж байна…"],
        [3, f"==> Аюулгүй байдлын шалгалт: {service.score}/100 ✓"],
        [4, "==> Python 3.12 орчин бэлдэж байна"],
        [6, "==> pip install -r requirements.txt"],
        [12, "    Сангууд амжилттай суулаа"],
        [14, f"==> Image бүтээж байна: aidolab/{service.slug}:{number}"],
        [18, env_line],
        # Demo never starts the app, so the log must not claim it is reachable.
        [20, "==> ТУРШИЛТЫН ГОРИМ: апп бодитоор эхлүүлээгүй"],
        [DEPLOY_SECONDS - 1, f"==> Шалгалт, бэлтгэл дууслаа. {service.url} хаяг бодит hosting идэвхжсэний дараа ажиллана."],
    ]


def deploy_error(service: Service, plan: dict) -> str | None:
    if profile_for(service.owner).free_expired:
        return f"Үнэгүй {FREE_DAYS} хоног дууссан. Апп-аа үргэлжлүүлэн ажиллуулахын тулд багцаа ахиулна уу."
    if service.score < MIN_DEPLOY_SCORE:
        return f"Deploy хийхийн тулд оноо {MIN_DEPLOY_SCORE}-аас дээш байх ёстой. Эхлээд асуудлуудаа засаад дахин шалгуулаарай."
    deployed = Service.objects.filter(owner=service.owner, deploys__isnull=False).exclude(pk=service.pk).distinct().count()
    if deployed >= plan["apps"]:
        return f"{plan['name']} багцад {plan['apps']} апп deploy хийх боломжтой. Багцаа ахиулна уу."
    return None


def start_deploy(service: Service, trigger: str = "manual") -> Deploy:
    number = (service.deploys.count() or 0) + 1
    profile = profile_for(service.owner)
    if profile.effective_plan == "free" and not profile.free_started_at:
        profile.free_started_at = timezone.now()
        profile.save(update_fields=["free_started_at"])
    service.suspended = False
    service.save(update_fields=["suspended"])
    if is_aws():
        # Rollback = rebuild the same repo; image reuse comes later.
        return Deploy.objects.create(service=service, number=number, trigger=trigger, score=service.score,
                                     state=Deploy.QUEUED, duration=0,
                                     logs=[[0, f"==> #{number} Deploy дараалалд орлоо · {service.repo_url}"],
                                           [0, f"==> Аюулгүй байдлын шалгалт: {service.score}/100 ✓"]])
    return Deploy.objects.create(service=service, number=number, trigger=trigger, score=service.score,
                                 logs=deploy_logs(service, number, trigger))


def set_suspended(service: Service, suspended: bool) -> None:
    service.suspended = suspended
    service.save(update_fields=["suspended"])
    if is_aws():
        from . import aws
        aws.scale(service, 0 if suspended else 1)


def expire_free_apps() -> int:
    """Stop the apps of accounts whose free period is over. Run by the worker; returns how many stopped.

    A paid plan that ran out (plan_until passed) also falls back to free: those accounts get
    the FREE_DAYS grace period counted from the day the plan ended.
    """
    now = timezone.now()
    lapsed = Profile.objects.exclude(plan="free").filter(plan_until__lt=now, free_started_at__isnull=True)
    for profile in lapsed:
        profile.free_started_at = profile.plan_until
        profile.save(update_fields=["free_started_at"])
    stopped = 0
    for profile in Profile.objects.filter(free_started_at__lte=now - timezone.timedelta(days=FREE_DAYS)):
        if not profile.free_expired:
            continue
        for service in Service.objects.filter(owner_id=profile.user_id, suspended=False, deploys__isnull=False).distinct():
            set_suspended(service, True)
            AuditEvent.objects.create(user_id=profile.user_id, service=service, service_name=service.name, kind="billing",
                                      message=f"Үнэгүй {FREE_DAYS} хоног дууссан тул апп автоматаар зогслоо")
            stopped += 1
    return stopped


def delete_service(service: Service) -> None:
    """Remove the record now; AWS resources are torn down by the worker (retries until clean)."""
    if is_aws():
        db = getattr(service, "database", None)
        Job.objects.create(kind="teardown", payload={
            "pk": service.pk, "slug": service.slug, "files": hasattr(service, "bucket"),
            "database": [db.name, db.username] if db else None})
    service.delete()


def delete_database(service: Service) -> None:
    db = service.database
    if is_aws():
        Job.objects.create(kind="drop_database", payload={"name": db.name, "username": db.username})
    db.delete()


def delete_bucket(service: Service) -> None:
    if is_aws():
        Job.objects.create(kind="delete_files", payload={"prefix": service.bucket.prefix})
    service.bucket.delete()


def create_database(service: Service) -> Database:
    base = re.sub(r"[^a-z0-9]", "_", service.slug)
    db = Database(service=service, name=f"db_{base}", username=f"u_{base}")
    if is_aws():
        from . import aws
        aws.create_database(db.name, db.username, db.password)
    db.save()
    return db


def create_bucket(service: Service) -> Bucket:
    return Bucket.objects.create(service=service)


def allocate_ip(user) -> str:
    """Pick an address from the 198.51.100.0/24 documentation range (simulated Elastic IP)."""
    return f"198.51.100.{10 + user.pk % 240}"


def gateway_ip() -> str:
    return settings.GATEWAY_IP


def app_log(service: Service) -> list[str] | None:
    if not is_aws() or service.latest_deploy is None:
        return None
    from . import aws
    return aws.app_log(service)


# --- Metrics (simulated) -------------------------------------------------------
# Deterministic per service and time bucket, so reloads show the same history.
# Replaced by CloudWatch Container Insights / ALB-style access logs when hosting is real.

METRIC_RANGES = {
    "1h": {"label": "1 цаг", "span": 3600, "step": 60, "fmt": "%H:%M"},
    "24h": {"label": "24 цаг", "span": 86400, "step": 900, "fmt": "%H:%M"},
    "7d": {"label": "7 хоног", "span": 7 * 86400, "step": 7200, "fmt": "%m/%d %H:%M"},
}
MEMORY_LIMIT_MB = 512


def _traffic_factor(local_hour: float) -> float:
    # Quiet at night, busy in the afternoon/evening (Ulaanbaatar time).
    return 0.15 + 0.85 * max(0.0, math.sin(math.pi * (local_hour - 7) / 16)) if 7 <= local_hour <= 23 else 0.15


def metrics(service: Service, range_key: str) -> dict | None:
    if is_aws():
        return real_metrics(service, range_key)
    first = service.deploys.order_by("started_at").first()
    if first is None:
        return None
    cfg = METRIC_RANGES[range_key]
    step = cfg["step"]
    end = int(timezone.now().timestamp()) // step * step
    base_rpm = 2 + service.pk % 9
    labels, rpm, cpu, mem, p50, p95, bw = [], [], [], [], [], [], []
    for t in range(end - cfg["span"] + step, end + step, step):
        when = timezone.localtime(timezone.datetime.fromtimestamp(t, tz=timezone.UTC))
        labels.append(when.strftime(cfg["fmt"]))
        if t < first.started_at.timestamp():
            for series in (rpm, cpu, mem, p50, p95, bw):
                series.append(None)
            continue
        rnd = random.Random(f"{service.pk}-{t // 60}")
        r = base_rpm * _traffic_factor(when.hour + when.minute / 60) * rnd.uniform(0.75, 1.25)
        rpm.append(round(r, 1))
        cpu.append(round(min(95, 3 + r * 1.8 + rnd.uniform(-1.5, 1.5)), 1))
        mem.append(round(min(MEMORY_LIMIT_MB - 20, 150 + r * 3 + rnd.uniform(-6, 6))))
        median = 40 + r * 1.6 + rnd.uniform(-5, 5)
        p50.append(round(median))
        p95.append(round(median * rnd.uniform(2.1, 2.8)))
        bw.append(round(r * step / 60 * 0.02, 2))  # ~20 KB per response
    live = [i for i, v in enumerate(rpm) if v is not None]

    def avg(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else 0

    summary = {
        "cpu": avg(cpu), "mem": round(avg(mem)), "p95": round(avg(p95)),
        "requests": round(sum(v * step / 60 for v in rpm if v is not None)),
        "bandwidth": round(sum(v for v in bw if v is not None), 1),
    }
    return {"labels": labels, "rpm": rpm, "cpu": cpu, "mem": mem, "p50": p50, "p95": p95, "bw": bw,
            "summary": summary, "has_data": bool(live), "memory_limit": MEMORY_LIMIT_MB,
            "bucket_label": {60: "минут", 900: "15 минут", 7200: "2 цаг"}[step]}


def real_metrics(service: Service, range_key: str) -> dict | None:
    """CPU and memory from CloudWatch. Request/latency series need nginx access-log parsing (later)."""
    if not service.deploys.filter(state=Deploy.LIVE).exists():
        return None
    from . import aws
    cfg = METRIC_RANGES[range_key]
    step = cfg["step"]
    end = int(timezone.now().timestamp()) // step * step
    times = list(range(end - cfg["span"] + step, end + step, step))
    data = aws.cpu_memory(service, timezone.datetime.fromtimestamp(times[0], tz=timezone.UTC),
                          timezone.datetime.fromtimestamp(end + step, tz=timezone.UTC), step)
    labels = [timezone.localtime(timezone.datetime.fromtimestamp(t, tz=timezone.UTC)).strftime(cfg["fmt"]) for t in times]
    cpu = [data["cpu"].get(t) for t in times]
    mem = [data["mem"].get(t) for t in times]
    empty = [None] * len(times)

    def avg(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else 0

    return {"labels": labels, "rpm": empty, "cpu": cpu, "mem": mem, "p50": empty, "p95": empty, "bw": empty,
            "summary": {"cpu": avg(cpu), "mem": round(avg(mem)), "p95": 0, "requests": 0, "bandwidth": 0},
            "has_data": any(v is not None for v in cpu), "memory_limit": MEMORY_LIMIT_MB,
            "bucket_label": {60: "минут", 900: "15 минут", 7200: "2 цаг"}[step]}
