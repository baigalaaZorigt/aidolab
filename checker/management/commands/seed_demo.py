"""Fill a workspace with realistic demo data (services, scans, deploys, DB, domains, mentor, billing).

    python manage.py seed_demo --email you@example.com

Repos live under the fake GitHub org `aidolab-demo`, so re-scanning them fails on purpose.
Classmate accounts get unusable passwords: they exist only to populate /admin/.
Re-running replaces earlier demo data.
"""
from dataclasses import asdict

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from checker.hosting import create_bucket, create_database, deploy_logs
from checker.models import (AuditEvent, CustomDomain, Deploy, EnvVar, Invoice, MentorRequest, ScanRun,
                            Service, profile_for)
from checker.plans import PLAN_DAYS, PLANS
from checker.scanner import Finding, score

ORG = "https://github.com/aidolab-demo/"
SEED_IP = "203.0.113.55"  # documentation range; marks seeded audit events for cleanup
CLASSMATES = [("bold@demo.aidolab.mn", "Болд"), ("saraa@demo.aidolab.mn", "Сараа")]

F = Finding
APPS = {
    "todo-app": {
        "findings": [
            F("bandit_B201", "medium", "A Flask app appears to be run with debug=True", "app.py", 58, "B201"),
            F("unpinned", "low", "Сангийн хувилбар тогтоогоогүй (==)", "requirements.txt", 0, "flask, requests"),
        ],
        "history": [52, 76],
        "deploys": 3, "database": True, "bucket": True, "domain": "www.bold-todo.mn",
        "env": {"OPENAI_API_KEY": "sk-demo-not-a-real-key", "FLASK_SECRET_KEY": "demo-secret-value"},
        "advice": {
            "summary": "Таны апп deploy хийхэд бэлэн байна! Хоёр жижиг зүйл үлдсэн — засвал бүр найдвартай болно.",
            "items": [
                {"title": "Flask debug горим", "why": "debug=True үед алдаа гарахад кодын дэлгэрэнгүй мэдээлэл хэн ч харах боломжтой болно.",
                 "fix": "app.run(debug=True)-г устгаад, debug-ийг орчны хувьсагчаар удирд:\n  app.run(debug=os.environ.get(\"FLASK_DEBUG\") == \"1\")"},
                {"title": "Сангийн хувилбар", "why": "Хувилбар тогтоогоогүй бол дараагийн deploy дээр шинэ хувилбар орж апп эвдэрч болно.",
                 "fix": "Терминалд: pip freeze > requirements.txt"},
            ],
            "next_step": "app.py-ийн 58-р мөрөнд debug=True-г орчны хувьсагчаар солих.",
        },
    },
    "weather-bot": {
        "findings": [
            F("django_debug", "high", "DEBUG = True шууд бичигдсэн", "config/settings.py", 26),
            F("allowed_hosts", "medium", "ALLOWED_HOSTS = ['*'] — бүх домэйнд нээлттэй", "config/settings.py", 28),
            F("no_wsgi", "medium", "Production сервер (gunicorn/uvicorn) requirements-д алга", "requirements.txt"),
            F("unpinned", "low", "Сангийн хувилбар тогтоогоогүй (==)", "requirements.txt", 0, "django, python-telegram-bot"),
        ],
        "history": [61],
        "advice": {
            "summary": "Апп ажиллаж байгаа нь сайн байна. Deploy хийхээс өмнө Django-ийн тохиргоогоо production-д тохируулах хэрэгтэй.",
            "items": [
                {"title": "DEBUG = True", "why": "Алдаа гарахад нууц тохиргоо, кодын зам зэрэг мэдээлэл хэрэглэгчид харагдана.",
                 "fix": "settings.py-д:\n  DEBUG = os.environ.get(\"DJANGO_DEBUG\") == \"1\""},
                {"title": "ALLOWED_HOSTS = ['*']", "why": "Ямар ч домэйноос таны сайт руу хандах боломжтой болж, зарим халдлагад өртөмтгий.",
                 "fix": "ALLOWED_HOSTS = [\"weather-bot.aidolab.mn\"]"},
                {"title": "gunicorn алга", "why": "runserver нь зөвхөн хөгжүүлэлтэд зориулагдсан, олон хэрэглэгчийг даахгүй.",
                 "fix": "requirements.txt-д gunicorn нэмээд start command: gunicorn config.wsgi"},
            ],
            "next_step": "settings.py дээр DEBUG-ийг орчны хувьсагчаас уншдаг болгох.",
        },
    },
    "shop-api": {
        "findings": [
            F("secret", "high", "OpenAI API key кодонд ил байна", "shop/ai.py", 7, "sk-p…******"),
            F("secret", "high", "'password' утга кодонд шууд бичигдсэн", "shop/db.py", 12, "Shop…******"),
            F("env_committed", "high", ".env файл GitHub-д орсон байна", ".env"),
            F("hardcoded_secret_key", "high", "SECRET_KEY кодонд шууд бичигдсэн", "shop/settings.py", 23),
            F("gitignore_env", "medium", ".gitignore дотор .env бүртгэгдээгүй", ".gitignore"),
            F("vulnerable_dep", "medium", "requests 2.25.0 — 2 мэдэгдэж буй эмзэг байдал", "requirements.txt", 0, "GHSA-j8r2-6x86-q33q"),
        ],
        "history": [26],
        "advice": {
            "summary": "Анхаар! Таны нууц түлхүүрүүд GitHub дээр ил харагдаж байна. Хамгийн түрүүнд түлхүүрүүдээ солих хэрэгтэй — бусад нь дараа нь.",
            "items": [
                {"title": "OpenAI түлхүүр ил байна", "why": "GitHub-ийн public repo-г бот байнга уншиж, олдсон түлхүүрээр таны данснаас мөнгө зарцуулдаг.",
                 "fix": "1. platform.openai.com дээр түлхүүрээ устгаж шинээр үүсгэ.\n2. Кодонд: os.environ[\"OPENAI_API_KEY\"]\n3. Шинэ түлхүүрээ aidolab-ын Орчны хувьсагч хэсэгт хадгал."},
                {"title": ".env файл GitHub-д орсон", "why": ".env доторх бүх нууц утга хүн бүрт харагдана.",
                 "fix": "git rm --cached .env\n.gitignore-д .env мөр нэмээд commit хий."},
                {"title": "requests сангийн эмзэг хувилбар", "why": "Хуучин хувилбарт мэдэгдэж буй аюулгүй байдлын алдаа бий.",
                 "fix": "requirements.txt-д requests==2.32.3 болгож шинэчил."},
            ],
            "next_step": "OpenAI түлхүүрээ ЯГ ОДОО устгаж шинээр үүсгэ.",
        },
    },
    "portfolio": {"findings": [], "history": [85], "deploys": 1, "advice": None},
}


def make_service(user, slug, spec, now):
    findings = [asdict(f) for f in spec["findings"]]
    svc = Service.objects.create(owner=user, name=f"aidolab-demo/{slug}", slug=slug, repo_url=ORG + slug,
                                 score=score(spec["findings"]), findings=findings, advice=spec.get("advice"))
    # Score history, oldest first, ending with the current scan.
    scores = spec["history"] + [svc.score]
    for i, s in enumerate(scores):
        run = ScanRun.objects.create(service=svc, score=s, high=max(0, 3 - i), medium=2, low=1)
        ScanRun.objects.filter(pk=run.pk).update(created_at=now - timezone.timedelta(days=len(scores) - i, hours=3))
    c = svc.counts
    svc.scans.filter(pk=svc.scans.first().pk).update(high=c["high"], medium=c["medium"], low=c["low"])
    for key, value in spec.get("env", {}).items():
        EnvVar.objects.create(service=svc, key=key, value=value)
    if spec.get("database"):
        db = create_database(svc)
        db.size_mb = 14
        db.save()
    if spec.get("bucket"):
        b = create_bucket(svc)
        b.used_mb = 37
        b.save()
    for n in range(1, spec.get("deploys", 0) + 1):
        d = Deploy.objects.create(service=svc, number=n, score=svc.score, logs=deploy_logs(svc, n, "manual"))
        Deploy.objects.filter(pk=d.pk).update(started_at=now - timezone.timedelta(days=spec["deploys"] - n, hours=1))
    if spec.get("domain"):
        CustomDomain.objects.create(service=svc, domain=spec["domain"], status=CustomDomain.VERIFIED, last_checked_at=now)
    Service.objects.filter(pk=svc.pk).update(updated_at=now - timezone.timedelta(hours=2))
    return svc


def seed_events(user, services, now):
    todo, weather, shop = services["todo-app"], services["weather-bot"], services["shop-api"]
    rows = [  # (hours ago, kind, message, service)
        (150, "auth", "Бүртгэл үүсгэв", None),
        (149, "scan", "Repo шалгав — 52/100 оноо", todo),
        (120, "scan", "Дахин шалгав — 76/100 оноо", todo),
        (100, "billing", "INV-демо төлөгдөж Standard багц идэвхжив (158,000₮)", None),
        (99, "env", "OPENAI_API_KEY хувьсагчийг нэмэв", todo),
        (98, "env", "FLASK_SECRET_KEY хувьсагчийг нэмэв", todo),
        (97, "database", f"Postgres өгөгдлийн сан {todo.database.name} үүсгэв", todo),
        (96, "storage", "Файлын сан идэвхжүүлэв", todo),
        (75, "scan", "Дахин шалгав — 90/100 оноо", todo),
        (74, "deploy", "Deploy #1 эхлүүлэв", todo),
        (73, "domain", "www.bold-todo.mn домэйн нэмэв", todo),
        (70, "domain", "www.bold-todo.mn домэйн баталгаажиж SSL идэвхжив", todo),
        (50, "deploy", "Deploy #2 эхлүүлэв", todo),
        (48, "scan", "Repo шалгав — 68/100 оноо", weather),
        (40, "scan", "Repo шалгав — 26/100 оноо", shop),
        (39, "mentor", "Mentor-оос асуув: Түлхүүр ил гарсан гэж байна", shop),
        (26, "deploy", "Deploy #3 эхлүүлэв", todo),
        (22, "mentor", "Mentor-оос асуув: DATABASE_URL-ийг яаж унших вэ?", todo),
        (3, "auth", "Нэвтэрлээ", None),
    ]
    for hours, kind, message, svc in rows:
        e = AuditEvent.objects.create(user=user, service=svc, service_name=svc.name if svc else "",
                                      kind=kind, message=message, ip=SEED_IP)
        AuditEvent.objects.filter(pk=e.pk).update(created_at=now - timezone.timedelta(hours=hours))


class Command(BaseCommand):
    help = "Fill a user's workspace with demo data"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Existing user (login email/username) to fill")

    def handle(self, *args, email, **opts):
        user = User.objects.filter(username=email.lower()).first() or User.objects.filter(email=email.lower()).first()
        if user is None:
            raise CommandError(f"{email} хэрэглэгч олдсонгүй. Эхлээд /signup/ дээр бүртгүүлнэ үү.")
        now = timezone.now()

        classmates = []
        for cm_email, name in CLASSMATES:
            cm, _ = User.objects.get_or_create(username=cm_email, defaults={"email": cm_email, "first_name": name})
            cm.set_unusable_password()
            cm.save()
            classmates.append(cm)

        targets = [user] + classmates
        Service.objects.filter(owner__in=targets, repo_url__startswith=ORG).delete()
        AuditEvent.objects.filter(user__in=targets, ip=SEED_IP).delete()
        MentorRequest.objects.filter(user__in=targets, topic__startswith="[demo] ").delete()
        Invoice.objects.filter(user__in=targets).filter(amount__in=[p["price"] for p in PLANS.values()]).delete()

        services = {slug: make_service(user, slug, spec, now) for slug, spec in APPS.items()}
        seed_events(user, services, now)

        profile = profile_for(user)
        profile.plan, profile.plan_until = "standard", now + timezone.timedelta(days=PLAN_DAYS - 4)
        profile.save()
        inv = Invoice.objects.create(user=user, plan="standard", amount=PLANS["standard"]["price"], paid_at=now - timezone.timedelta(days=4))
        Invoice.objects.filter(pk=inv.pk).update(created_at=now - timezone.timedelta(days=4))

        MentorRequest.objects.create(
            user=user, service=services["todo-app"], topic="[demo] DATABASE_URL-ийг яаж унших вэ?",
            question="Өгөгдлийн сан үүсгэсэн. Flask апп-аасаа яаж холбох вэ?",
            answer="os.environ[\"DATABASE_URL\"]-г SQLAlchemy-д өгөөрэй:\n  app.config[\"SQLALCHEMY_DATABASE_URI\"] = os.environ[\"DATABASE_URL\"]\nДараа нь дахин deploy хий.",
            answered_at=now - timezone.timedelta(hours=20))
        MentorRequest.objects.create(
            user=user, service=services["shop-api"], topic="[demo] Түлхүүр ил гарсан гэж байна",
            question="shop-api дээр OpenAI түлхүүр ил байна гэсэн. Repo-гоо устгах хэрэгтэй юу?")

        # Classmates: one service each plus a pending question for the teacher's /admin/ queue.
        for cm, slug, spec in ((classmates[0], "bold-quiz", APPS["weather-bot"]), (classmates[1], "saraa-blog", APPS["portfolio"])):
            make_service(cm, slug, {**spec, "deploys": spec.get("deploys", 0)}, now)
            MentorRequest.objects.create(user=cm, topic="[demo] gunicorn гэж юу вэ?",
                                         question="Шалгалт gunicorn алга гэж байна. Юу хийх вэ?")

        self.stdout.write(self.style.SUCCESS(
            f"{user.username}: {len(services)} үйлчилгээ, deploy, DB, файлын сан, домэйн, mentor, системийн лог, Standard багц нэмэгдлээ. "
            f"Ангийн 2 хүүхэд (нэвтрэх эрхгүй) /admin/-д харагдана."))
