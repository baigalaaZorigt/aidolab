import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import hosting
from .ai import explain
from .audit import audit
from .models import CustomDomain, EnvVar, ScanRun, Service, profile_for
from .naming import dns_lookup, domain_error, normalize_domain, slug_error, slug_from_repo
from .plans import get_plan
from .scanner import ScanError, scan_repo

ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
# Set by the platform at deploy time; students can't override them.
PLATFORM_ENV_KEYS = {"DATABASE_URL", "S3_BUCKET", "S3_PREFIX", "PORT"}


def repo_name(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").split("github.com/")[-1]


def unique_slug(base: str) -> str:
    if slug_error(base):
        base = "app"
    slug, n = base, 2
    while slug_error(slug) or Service.objects.filter(slug=slug).exists():
        slug = f"{base[:36].rstrip('-')}-{n}"
        n += 1
    return slug


def run_scan(user, url: str) -> Service:
    result = scan_repo(url)
    advice = explain(result) if result["findings"] else None
    name = repo_name(result["url"])
    service = Service.objects.filter(owner=user, repo_url=result["url"]).first()
    if service is None:
        service = Service(owner=user, repo_url=result["url"], slug=unique_slug(slug_from_repo(name)))
    service.name, service.score = name, result["score"]
    service.findings, service.advice = result["findings"], advice
    service.save()
    c = service.counts
    ScanRun.objects.create(service=service, score=service.score, high=c["high"], medium=c["medium"], low=c["low"])
    return service


def own_service(request, pk) -> Service:
    return get_object_or_404(Service, pk=pk, owner=request.user)


def user_plan(request) -> dict:
    return get_plan(profile_for(request.user).effective_plan)


@login_required
def dashboard(request):
    services = list(request.user.services.all())
    ready = sum(1 for s in services if s.status == "ready")
    return render(request, "checker/dashboard.html", {
        "services": services, "ready_count": ready, "todo_count": len(services) - ready,
        "live_count": sum(1 for s in services if s.deploy_state == "live"),
    })


@login_required
def new_service(request):
    context = {"repo_url": request.GET.get("repo", "")}
    if request.method == "POST":
        context["repo_url"] = request.POST.get("repo_url", "")
        try:
            service = run_scan(request.user, context["repo_url"])
        except ScanError as e:
            context["error"] = str(e)
            return render(request, "checker/new.html", context)
        audit(request, "scan", f"Repo шалгав — {service.score}/100 оноо", service)
        return redirect("service", pk=service.pk)
    return render(request, "checker/new.html", context)


@login_required
def service_overview(request, pk):
    service = own_service(request, pk)
    return render(request, "checker/service_overview.html", {"service": service, "tab": "overview"})


@login_required
def service_events(request, pk):
    service = own_service(request, pk)
    scans = list(service.scans.all()[:50])
    # Pair each scan with the previous one to show the score change.
    for scan, prev in zip(scans, scans[1:] + [None]):
        scan.delta = scan.score - prev.score if prev else None
        scan.kind, scan.at = "scan", scan.created_at
    deploys = list(service.deploys.all()[:50])
    for d in deploys:
        d.kind, d.at = "deploy", d.started_at
    current = deploys[0].pk if deploys else None
    events = sorted(scans + deploys, key=lambda e: e.at, reverse=True)
    return render(request, "checker/service_events.html", {
        "service": service, "events": events, "current_deploy": current, "tab": "events"})


@login_required
def service_logs(request, pk):
    service = own_service(request, pk)
    deploy = service.latest_deploy
    return render(request, "checker/service_logs.html", {"service": service, "deploy": deploy, "tab": "logs"})


@login_required
def service_metrics(request, pk):
    service = own_service(request, pk)
    range_key = request.GET.get("range", "24h")
    if range_key not in hosting.METRIC_RANGES:
        range_key = "24h"
    return render(request, "checker/service_metrics.html", {
        "service": service, "tab": "metrics", "range": range_key, "ranges": hosting.METRIC_RANGES,
        "data": hosting.metrics(service, range_key)})


@require_POST
@login_required
def deploy(request, pk):
    service = own_service(request, pk)
    error = hosting.deploy_error(service, user_plan(request))
    if error:
        messages.error(request, error)
        return redirect("service", pk=service.pk)
    d = hosting.start_deploy(service, trigger=request.POST.get("trigger", "manual"))
    audit(request, "deploy", f"Deploy #{d.number} эхлүүлэв" + (" (өмнөх хувилбар руу буцаах)" if d.trigger == "rollback" else ""), service)
    return redirect("service_logs", pk=service.pk)


@require_POST
@login_required
def suspend(request, pk):
    service = own_service(request, pk)
    service.suspended = not service.suspended
    service.save(update_fields=["suspended"])
    messages.success(request, "Апп түр зогслоо." if service.suspended else "Апп дахин асаалаа.")
    audit(request, "service", "Апп-ыг түр зогсоов" if service.suspended else "Апп-ыг дахин асаав", service)
    return redirect("service_settings", pk=service.pk)


@login_required
def service_database(request, pk):
    service = own_service(request, pk)
    plan = user_plan(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create" and not hasattr(service, "database"):
            if not plan["database"]:
                messages.error(request, "Өгөгдлийн сан Сурагч болон Pro багцад багтдаг.")
            else:
                db = hosting.create_database(service)
                audit(request, "database", f"Postgres өгөгдлийн сан {db.name} үүсгэв", service)
                messages.success(request, "Postgres өгөгдлийн сан үүслээ. DATABASE_URL дараагийн deploy-оос автоматаар орно.")
        elif action == "delete" and hasattr(service, "database"):
            audit(request, "database", f"Өгөгдлийн сан {service.database.name} устгав", service)
            service.database.delete()
            messages.success(request, "Өгөгдлийн сан устгагдлаа.")
        return redirect("service_database", pk=service.pk)
    return render(request, "checker/service_database.html", {
        "service": service, "db": getattr(service, "database", None), "tab": "database"})


@login_required
def service_storage(request, pk):
    service = own_service(request, pk)
    plan = user_plan(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create" and not hasattr(service, "bucket"):
            if not plan["storage_mb"]:
                messages.error(request, "Файл хадгалах сан Сурагч болон Pro багцад багтдаг.")
            else:
                hosting.create_bucket(service)
                audit(request, "storage", "Файлын сан идэвхжүүлэв", service)
                messages.success(request, "Файл хадгалах сан идэвхжлээ.")
        elif action == "delete" and hasattr(service, "bucket"):
            service.bucket.delete()
            audit(request, "storage", "Файлын сан устгав", service)
            messages.success(request, "Файл хадгалах сан устгагдлаа.")
        return redirect("service_storage", pk=service.pk)
    bucket = getattr(service, "bucket", None)
    used_pct = round(100 * bucket.used_mb / plan["storage_mb"]) if bucket and plan["storage_mb"] else 0
    return render(request, "checker/service_storage.html", {
        "service": service, "bucket": bucket, "used_pct": used_pct, "tab": "storage"})


@login_required
def service_env(request, pk):
    service = own_service(request, pk)
    if request.method == "POST":
        action = request.POST.get("action")
        key = request.POST.get("key", "").strip().upper()
        if action == "delete":
            service.env_vars.filter(key=key).delete()
            audit(request, "env", f"{key} хувьсагчийг устгав", service)
            messages.success(request, f"{key} устгагдлаа.")
        elif not ENV_KEY_RE.match(key):
            messages.error(request, "Нэр зөвхөн ТОМ латин үсэг, тоо, доогуур зураас (_) агуулна. Жишээ: OPENAI_API_KEY")
        elif key in PLATFORM_ENV_KEYS:
            messages.error(request, f"{key}-г систем автоматаар тохируулдаг тул өөрчлөх боломжгүй.")
        elif not request.POST.get("value"):
            messages.error(request, "Утга хоосон байна.")
        else:
            _, created = EnvVar.objects.update_or_create(
                service=service, key=key, defaults={"value": request.POST["value"]})
            messages.success(request, f"{key} {'нэмэгдлээ' if created else 'шинэчлэгдлээ'}.")
            audit(request, "env", f"{key} хувьсагчийг {'нэмэв' if created else 'шинэчлэв'}", service)
        return redirect("service_env", pk=service.pk)
    return render(request, "checker/service_env.html", {
        "service": service, "env_vars": service.env_vars.all(), "tab": "env",
        "platform_keys": [("PORT", True), ("DATABASE_URL", hasattr(service, "database")),
                          ("S3_BUCKET", hasattr(service, "bucket")), ("S3_PREFIX", hasattr(service, "bucket"))],
    })


def verify_domain(domain: CustomDomain) -> None:
    records = dns_lookup(domain.txt_name, "TXT")
    if records is not None:
        if domain.token in records:
            domain.status = CustomDomain.VERIFIED
        elif timezone.now() - domain.created_at > timezone.timedelta(hours=48):
            domain.status = CustomDomain.FAILED
    domain.last_checked_at = timezone.now()
    domain.save()


@login_required
def service_domains(request, pk):
    service = own_service(request, pk)
    limit = user_plan(request)["domains"]
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            name = normalize_domain(request.POST.get("domain", ""))
            error = domain_error(name)
            if error:
                messages.error(request, error)
            elif service.domains.count() >= limit:
                messages.error(request, "Таны багцад өөр домэйн холбох эрх алга. Багцаа ахиулна уу." if limit == 0
                               else f"Таны багцад нэг апп {limit} домэйн холбох боломжтой.")
            else:
                try:
                    CustomDomain.objects.create(service=service, domain=name)
                    audit(request, "domain", f"{name} домэйн нэмэв", service)
                    messages.success(request, f"{name} нэмэгдлээ. Доорх DNS бичлэгүүдийг нэмнэ үү.")
                except IntegrityError:
                    messages.error(request, "Энэ домэйн өөр апп-д холбогдсон байна.")
        else:
            domain = get_object_or_404(CustomDomain, pk=request.POST.get("domain_id"), service=service)
            if action == "verify":
                verify_domain(domain)
                if domain.status == CustomDomain.VERIFIED:
                    messages.success(request, f"{domain.domain} баталгаажлаа.")
                    audit(request, "domain", f"{domain.domain} домэйн баталгаажиж SSL идэвхжив", service)
                else:
                    messages.error(request, "TXT бичлэг одоогоор олдсонгүй. DNS шинэчлэгдэхэд хэдэн минутаас 48 цаг хүртэл болно.")
            elif action == "delete":
                domain.delete()
                audit(request, "domain", f"{domain.domain} домэйн устгав", service)
                messages.success(request, f"{domain.domain} устгагдлаа.")
        return redirect("service_domains", pk=service.pk)
    return render(request, "checker/service_domains.html", {
        "service": service, "domains": service.domains.all(), "limit": limit, "tab": "domains",
        "gateway_ip": hosting.gateway_ip(),
    })


@login_required
def service_settings(request, pk):
    service = own_service(request, pk)
    if request.method == "POST":
        slug = request.POST.get("slug", "").strip().lower()
        error = slug_error(slug)
        if error:
            messages.error(request, error)
        elif Service.objects.filter(slug=slug).exclude(pk=service.pk).exists():
            messages.error(request, "Энэ нэрийг өөр хүн ашиглаж байна.")
        else:
            old = service.slug
            service.slug = slug
            service.save(update_fields=["slug"])
            audit(request, "service", f"Хаягийг {old}.aidolab.mn → {slug}.aidolab.mn болгов", service)
            messages.success(request, f"Хаяг {service.url} боллоо.")
        return redirect("service_settings", pk=service.pk)
    return render(request, "checker/service_settings.html", {"service": service, "tab": "settings"})


@require_POST
@login_required
def rescan(request, pk):
    service = own_service(request, pk)
    try:
        run_scan(request.user, service.repo_url)
        service.refresh_from_db()
        audit(request, "scan", f"Дахин шалгав — {service.score}/100 оноо", service)
    except ScanError as e:
        messages.error(request, str(e))
    return redirect("service", pk=service.pk)


@require_POST
@login_required
def delete_service(request, pk):
    service = own_service(request, pk)
    audit(request, "service", f"{service.name} үйлчилгээг устгав", service)
    service.delete()
    messages.success(request, f"{service.name} устгагдлаа.")
    return redirect("dashboard")


@login_required
def deploys_list(request):
    services = [s for s in request.user.services.all() if s.latest_deploy]
    return render(request, "checker/hosting_deploys.html", {"services": services})


@login_required
def databases_list(request):
    services = request.user.services.filter(database__isnull=False)
    return render(request, "checker/hosting_databases.html", {"services": services})


@login_required
def storage_list(request):
    services = request.user.services.filter(bucket__isnull=False)
    return render(request, "checker/hosting_storage.html", {"services": services})


@login_required
def metrics_overview(request):
    rows = []
    for s in request.user.services.all():
        data = hosting.metrics(s, "24h")
        if data and data["has_data"]:
            rows.append((s, data["summary"]))
    return render(request, "checker/metrics_overview.html", {"rows": rows})
