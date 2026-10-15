# Deploy Шалгагч (vibecheck)

## Санаа

AI ашиглан (vibe coding) апп хийж сурч буй хүмүүс олширч байна. Тэд код бичиж чаддаг болсон ч **deploy, аюулгүй байдал, тогтвортой ажиллагаа** дээр гацдаг. Энэ систем тэдэнд:

1. Аппаа deploy хийхээс өмнө **шалгуулах**: нууц түлхүүр, `.env`, тохиргоо, эмзэг сан, аюултай код
2. Үр дүнг **монголоор, эхлэн суралцагчид ойлгомжтой** тайлбарлаж, засах алхмыг нь заах
3. Дараагийн шатанд: **hosting** (deploy хийх), **mentor-ийн дэмжлэг**

**Хэрэглэгч:** AI Academy Asia-гийн сурагчид (насанд хүрэгч, хүүхэд). Python дээр апп хийдэг. (Өмнө нь Render ашигладаг байсан; бид Render-тэй төстэй өөрсдийн платформыг хийж байна. **Render-ийг огт ашиглахгүй** — бүх байршуулалт AWS дээр.)

**Render-ээс ялгарах зүйл:** монгол хэл, төгрөгөөр төлөх / хямд үнэ, deploy-оос өмнөх автомат шалгалт + AI зөвлөгөө, mentor-ийн дэмжлэг.

**Эзэмшигч:** Baigalmaa Zorigt — AI Academy Asia-гийн Engineer Manager, AI багш.

## Төлөвлөгөө

| Шат | Хугацаа | Агуулга | Төлөв |
| --- | --- | --- | --- |
| 1. MVP — шалгагч | 2026-10-17-ны хичээлээс өмнө | GitHub repo шалгах, 100 онооны үнэлгээ, монгол AI зөвлөгөө. Dashboard `app.aidolab.mn`-ийг AWS EC2 дээр (`deploy/`) | Код бэлэн, локал дээр ажиллаж байгаа |
| 2. Ангид турших | 10/17-оос хойш | Сурагчдаас санал авах. Дасгал: "оноогоо 80-аас дээш гаргаад deploy хий" | — |
| 3. Hosting | Дараа | AWS дээр Python аппыг автоматаар байршуулах. Архитектур болон ажлын жагсаалт: `HOSTING_PLAN.md` (gateway EC2 + Fargate Spot + RDS micro + S3; апп бүр `<нэр>.aidolab.mn`, хэрэглэгчийн домэйн + автомат SSL) | Архитектур сонгосон (2026-10-11) |
| 4. Төлбөр | Дараа | QPay зэргээр төгрөгөөр төлөх | — |

**10/17-ноос өмнө хийх:** GitHub руу push → Anthropic API түлхүүр → AWS EC2 + Elastic IP + `app.aidolab.mn` DNS → `deploy/README.md`-ийн дагуу суулгах (`ACCESS_CODE` заавал) → сурагчийн бодит repo-гоор турших.

## Архитектур

- **Django** + SQLite. `config/` нь тохиргоо, `checker/` нь апп. UI нь Render-ийн dashboard шиг (цайвар), брэнд нь **aidolab**: нэвтрэх/бүртгүүлэх → Тойм → үйлчилгээ (табууд: Тойм, Үйл явдал, Орчны хувьсагч, Домэйн, Тохиргоо) → Бүртгэл, Төлбөр.
- **Демо горим (`DEMO_MODE`, анхдагчаар асаалттай):** "Удахгүй" гэсэн зүйл байхгүй, бүх цэс ажилладаг. Deploy (хугацаагаар тооцсон төлөв + log), Postgres, файлын сан, QPay нэхэмжлэх нь `checker/hosting.py`, `checker/workspace.py`-д **симуляц** (бодит нөөц үүсгэхгүй, мөнгө авахгүй). Дээд мөрөнд "Демо горим" шошго гарна, `DEMO_MODE=0` нууна. Бодит: шалгалт, бүртгэл, орчны хувьсагч, домэйний TXT шалгалт, Mentor (багш `/admin/`-аас хариулна), **Системийн лог** (`AuditEvent`, `checker/audit.py`: шинэ үйлдэл нэмэхдээ `audit()` дуудна, нууц утга бүү бич; admin дээр зөвхөн уншина, CSV экспорттой). Dedicated IP (Pro) нь демо. **Metrics** (апп-ын таб + `/metrics/` тойм): CPU, санах ой, хүсэлт, p50/p95, урсгал — `hosting.metrics()` deploy-оос хойших хугацаанд тогтвортой (seed-тэй) симуляц өгөгдөл үүсгэнэ; графикууд vanilla SVG (`service_metrics.html`), hover + гараар сонгох + хүснэгт харагдацтай. Багцын үнэ/хязгаар: `checker/plans.py`. Deploy хийхэд оноо ≥ 80 шаардлагатай.
- Нэвтрэлт: Django auth, имэйлийг `username`-ээр хадгална (`checker/accounts.py`). `ACCESS_CODE` нь бүртгүүлэх үеийн ангийн код. Багш `/admin/` (superuser: `manage.py createsuperuser`).
- `checker/models.py` — `Service` (owner, `slug` → `<slug>.aidolab.mn`, сүүлийн шалгалт), `ScanRun` (түүх), `EnvVar` (UI дээр зөвхөн бичнэ, утгыг харуулахгүй), `CustomDomain` (TXT `_aidolab.<домэйн>` токеноор баталгаажуулна).
- `checker/naming.py` — апп-ын нэрийн дүрэм (нөөцлөгдсөн, банк/төрийн нэр хориглох), домэйн шалгах, DNS-over-HTTPS (dns.google) lookup.
- Өгөгдөл: SQLite (`DATABASE_PATH`, production-д `/srv/aidolab/data/db.sqlite3` EBS дээр, шөнө бүр backup). Сурагчийн апп-ын DB-ийг RDS дээр гаргах үед dashboard-ийг ч RDS руу шилжүүлнэ.
- `checker/scanner.py` — repo-г `git clone --depth 1` хийж түр хавтсанд татаад шалгана:
  - нууц түлхүүрийн regex (утгыг нь `mask()`-аар далдална)
  - `.env` орсон эсэх, `.gitignore`, `DEBUG = True`, `SECRET_KEY`, `ALLOWED_HOSTS = ['*']`
  - `requirements.txt`: хувилбар тогтоосон эсэх, gunicorn/uvicorn байгаа эсэх, OSV API-аар эмзэг байдал
  - `bandit` (medium болон түүнээс дээш)
  - Оноо: 100-аас хасна (high −15, medium −7, low −3; нэг төрлийн асуудал 2-оос олон удаа хасагдахгүй)
- `checker/ai.py` — Claude (`claude-opus-5-5`, structured output, `fallbacks: "default"`) илэрцийг монголоор тайлбарлана. `ANTHROPIC_API_KEY` байхгүй эсвэл алдаа гарвал `None` буцааж, зөвлөгөөгүйгээр үр дүнг харуулна.
- `checker/views.py` — dashboard, new, үйлчилгээний табууд, rescan, delete. Бүх view `login_required`, зөвхөн өөрийн үйлчилгээг харна.
- `deploy/` — AWS gateway EC2 (Ubuntu 24.04 arm64) дээр байршуулах: `bootstrap.sh` (анхны суулгалт), `update.sh` (шинэ код), systemd gunicorn (`--timeout 180`), nginx + certbot (HTTPS, `/static/`; ELB байхгүй), SQLite backup timer. Заавар: `deploy/README.md`.

## Аюулгүй байдлын зарчим (заавал баримтлах)

- Сурагчийн кодыг **хэзээ ч ажиллуулахгүй**: зөвхөн уншина, static анализ хийнэ. `pip install` хийхгүй (setup.py ажиллах эрсдэлтэй). Сангийн эмзэг байдлыг зөвхөн OSV API-аар шалгана.
- Clone хийхдээ `core.symlinks=false` ашиглана. Repo-гийн хэмжээ 50MB-аас ихгүй байна.
- Нууц утгыг Claude-д болон дэлгэцэнд **бүтнээр нь хэзээ ч илгээхгүй, харуулахгүй**. Зөвхөн далдалсан хэлбэрээр дамжуулна.
- Бүх нууц мэдээлэл орчны хувьсагчаас (env var) ирнэ: `DJANGO_SECRET_KEY`, `ANTHROPIC_API_KEY`, `ACCESS_CODE`, `DJANGO_DEBUG`, `ALLOWED_HOSTS`.

## Ажиллуулах

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DJANGO_DEBUG=1 .venv/bin/python manage.py migrate
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver 8765   # 8000 порт өөр програмд эзлэгдсэн
```

AI зөвлөгөө харахын тулд: `export ANTHROPIC_API_KEY=...`

Туршилтын өгөгдөл (бүртгэлтэй хэрэглэгчийн ажлын талбарыг дүүргэнэ, дахин ажиллуулахад солигдоно): `.venv/bin/python manage.py seed_demo --email <имэйл>`

## Код бичих дүрэм

- Хэрэглэгчид харагдах бүх текст **монгол** хэл дээр байна. Код, тайлбар (comment) **англи** хэл дээр байна.
- Энгийн байлгах: шинэ framework, frontend build, өгөгдлийн сан нэмэхээс өмнө эзэмшигчээс асуух.
- Шинэ шалгалт нэмэхдээ `Finding(id, severity, title, file, line, detail)` хэлбэрээр буцаана.
