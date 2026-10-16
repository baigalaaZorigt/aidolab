import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

PLATFORM_DOMAIN = "aidolab.mn"


def ago(when) -> str:
    seconds = int((timezone.now() - when).total_seconds())
    for unit, label in ((86400 * 30, "сар"), (86400, "өдөр"), (3600, "цаг"), (60, "мин")):
        if seconds >= unit:
            return f"{seconds // unit} {label}"
    return "Саяхан"


class Service(models.Model):
    """A student's repo, its latest scan, and (later) its hosted app."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=40, unique=True)
    repo_url = models.CharField(max_length=500)
    score = models.IntegerField(default=0)
    # Findings only ever contain masked secret values (see scanner.mask).
    findings = models.JSONField(default=list)
    advice = models.JSONField(null=True, blank=True)
    suspended = models.BooleanField(default=False)
    use_dedicated_ip = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "repo_url"], name="unique_repo_per_owner")]

    @property
    def latest_deploy(self):
        return self.deploys.first()

    @property
    def deploy_state(self) -> str:
        """none | building | live | failed | suspended"""
        latest = self.latest_deploy
        if latest is None:
            return "none"
        if self.suspended:
            return "suspended"
        return latest.status

    @property
    def deploy_label(self) -> str:
        return {"none": "Deploy хийгдээгүй", "building": "Deploy хийгдэж байна",
                "live": "Ажиллаж байна", "failed": "Deploy амжилтгүй",
                "suspended": "Түр зогссон"}[self.deploy_state]

    def __str__(self):
        return self.name

    @property
    def url(self) -> str:
        return f"https://{self.slug}.{PLATFORM_DOMAIN}"

    @property
    def status(self) -> str:
        if self.score >= 80:
            return "ready"
        return "warn" if self.score >= 50 else "risk"

    @property
    def status_label(self) -> str:
        return {"ready": "Deploy хийхэд бэлэн", "warn": "Засах шаардлагатай", "risk": "Эрсдэл өндөр"}[self.status]

    @property
    def counts(self) -> dict:
        return {sev: sum(1 for f in self.findings if f["severity"] == sev) for sev in ("high", "medium", "low")}

    @property
    def updated_ago(self) -> str:
        return ago(self.updated_at)


class ScanRun(models.Model):
    """One scan in a service's history, shown on the Events tab."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="scans")
    score = models.IntegerField()
    high = models.IntegerField(default=0)
    medium = models.IntegerField(default=0)
    low = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def ago(self) -> str:
        return ago(self.created_at)


class EnvVar(models.Model):
    """App environment variable. Write-only in the UI; moves to SSM when hosting starts."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="env_vars")
    key = models.CharField(max_length=100)
    value = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["service", "key"], name="unique_env_key")]


class CustomDomain(models.Model):
    PENDING, VERIFIED, FAILED = "pending", "verified", "failed"

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField(max_length=253, unique=True)
    token = models.CharField(max_length=64, default=secrets.token_hex)
    status = models.CharField(max_length=10, default=PENDING)
    # Real hosting only: certificate state reported by deploy/sync-domains.py ("" until TXT is verified).
    ssl_status = models.CharField(max_length=10, blank=True, default="")
    ssl_error = models.CharField(max_length=300, blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    @property
    def txt_name(self) -> str:
        return f"_aidolab.{self.domain}"

    @property
    def is_apex(self) -> bool:
        return self.domain.count(".") == 1


DEPLOY_SECONDS = 25


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    plan = models.CharField(max_length=10, default="free")
    plan_until = models.DateTimeField(null=True, blank=True)

    @property
    def effective_plan(self) -> str:
        if self.plan != "free" and self.plan_until and self.plan_until < timezone.now():
            return "free"
        return self.plan


def profile_for(user) -> Profile:
    return Profile.objects.get_or_create(user=user)[0]


class Deploy(models.Model):
    """A deploy. Demo mode: status comes from elapsed time and logs are revealed by offset.
    AWS mode (`state` set): the worker moves it queued → building → starting → live | failed
    and appends log lines as they happen."""

    QUEUED, BUILDING, STARTING, LIVE, FAILED = "queued", "building", "starting", "live", "failed"

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="deploys")
    number = models.IntegerField()
    trigger = models.CharField(max_length=20, default="manual")
    score = models.IntegerField(default=0)
    logs = models.JSONField(default=list)  # [[offset_seconds, line], ...]
    started_at = models.DateTimeField(default=timezone.now)
    duration = models.IntegerField(default=DEPLOY_SECONDS)
    state = models.CharField(max_length=10, blank=True)  # empty = simulated
    build_id = models.CharField(max_length=200, blank=True)
    image = models.CharField(max_length=300, blank=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]

    @property
    def finished_at(self):
        return self.started_at + timezone.timedelta(seconds=self.duration)

    @property
    def is_real(self) -> bool:
        return bool(self.state)

    @property
    def status(self) -> str:
        """building | live | failed"""
        if self.state:
            return {self.LIVE: "live", self.FAILED: "failed"}.get(self.state, "building")
        return "building" if timezone.now() < self.finished_at else "live"

    def log(self, line: str) -> None:
        self.logs.append([self.elapsed, line])
        self.save(update_fields=["logs"])

    @property
    def elapsed(self) -> int:
        return int((timezone.now() - self.started_at).total_seconds())

    @property
    def ago(self) -> str:
        return ago(self.started_at)


class Database(models.Model):
    service = models.OneToOneField(Service, on_delete=models.CASCADE, related_name="database")
    name = models.CharField(max_length=63)
    username = models.CharField(max_length=63)
    password = models.CharField(max_length=64, default=secrets.token_urlsafe)
    size_mb = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    HOST = "db.aidolab.internal"

    @property
    def url(self) -> str:
        return f"postgresql://{self.username}:{self.password}@{self.HOST}:5432/{self.name}"


class Bucket(models.Model):
    service = models.OneToOneField(Service, on_delete=models.CASCADE, related_name="bucket")
    used_mb = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    BUCKET = "aidolab-files"

    @property
    def prefix(self) -> str:
        return f"{self.service.slug}/"


class Invoice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invoices")
    plan = models.CharField(max_length=10)
    amount = models.IntegerField()
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def number(self) -> str:
        return f"INV-{self.pk:05d}"

    @property
    def amount_label(self) -> str:
        return f"{self.amount:,}₮"


class MentorRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mentor_requests")
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    topic = models.CharField(max_length=200)
    question = models.TextField()
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def ago(self) -> str:
        return ago(self.created_at)


class AuditEvent(models.Model):
    """Workspace activity log. Never store secret values here — only names (e.g. env var keys)."""

    KINDS = {
        "auth": "Нэвтрэлт", "service": "Үйлчилгээ", "scan": "Шалгалт", "deploy": "Deploy",
        "env": "Орчны хувьсагч", "database": "Өгөгдлийн сан", "storage": "Файл", "domain": "Домэйн",
        "network": "Сүлжээ", "billing": "Төлбөр", "mentor": "Mentor",
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="audit_events")
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    service_name = models.CharField(max_length=200, blank=True)  # kept after the service is deleted
    kind = models.CharField(max_length=20)
    message = models.CharField(max_length=500)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def kind_label(self) -> str:
        return self.KINDS.get(self.kind, self.kind)


class DedicatedIP(models.Model):
    """Static outbound IP for a workspace (Pro). Simulated until the NAT/EIP setup exists."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dedicated_ip")
    address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)


class Job(models.Model):
    """Background AWS work that must outlive the request (e.g. tearing down a deleted app)."""

    kind = models.CharField(max_length=30)
    payload = models.JSONField(default=dict)
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    done = models.BooleanField(default=False)
    run_after = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run_after", "pk"]
