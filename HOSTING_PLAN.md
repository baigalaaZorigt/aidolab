# Hosting төлөвлөгөө — ажлын жагсаалт

> **2026-10-16 шинэчлэл:** Caddy-ийн оронд **nginx + certbot**, ELB байхгүй. Дэд бүтцийг **Terraform** (`infra/`) үүсгэнэ.
> Код: `checker/aws.py` (AWS дуудлага), `checker/worker.py` (deploy queue), `HOSTING_BACKEND=aws|demo`.
> Апп-ын маршрут: nginx `<slug>.aidolab.mn` → Cloud Map `<slug>.apps.aidolab.internal:8000`. Хэрэглэгчийн домэйн (certbot HTTP-01, nginx map) — дараагийн алхам.

**Шийдвэр (2026-10-11):** хамгийн хямд хэрнээ найдвартай архитектур буюу **"B хувилбар"** ашиглана.
Нэг жижиг EC2 сервер манай кодыг (dashboard, worker, proxy) ажиллуулна. Апп бүр `<нэр>.aidolab.mn` хаягтай, хэрэглэгч өөрийн домэйнээ холбож болно, SSL автомат. Сурагчийн апп Fargate Spot дээр тусдаа ажиллана.
Өгөгдлийн сангаар хуваалцсан RDS Postgres, файл хадгалахад S3 ашиглана.

## Архитектур

```
Сурагч ─HTTPS─▶ Gateway EC2 (t4g.small, Elastic IP)
                 ├─ Django dashboard + worker   (манай код, сурагчийн код ЭНД ажиллахгүй)
                 ├─ Caddy proxy: *.aidolab.mn + хэрэглэгчийн домэйн → апп (автомат SSL)
                 └─ NAT: private subnet-ийн гарах урсгал
                         │
   Private subnet ───────┼──────────────────────────────────────────────
                         ├─▶ ECS Fargate Spot: сурагчийн апп (0.25 vCPU / 0.5GB)
                         │     env: DATABASE_URL, S3_BUCKET, S3_PREFIX (SSM-ээс)
                         ├─▶ RDS Postgres t4g.micro: апп бүрд тусдаа database + role
                         └─▶ S3: апп бүрд prefix + IAM task role
   Build: CodeBuild (нэг удаагийн орчин) → ECR
   Log: CloudWatch (7 хоног хадгална) → dashboard-д харуулна
```

## Зардлын тооцоо (Tokyo/Seoul бүс, ойролцоо)

| | Сард |
|---|---|
| Суурь зардал (EC2 + IP ~$20, RDS micro ~$17, домэйн + DNS ~$2, бусад ~$3) | **~$42** |
| Сурагч бүрийн хувьсах зардал (Fargate Spot ~$3.5, AI ~$2, бусад ~$1) | **~$6** |
| 20 сурагч бол нэгд ногдох | ~$8 (~28,000₮) |
| 50 сурагч бол нэгд ногдох | ~$6.8 (~24,000₮) |
| Апп унтраах болон AI-г хүсэх үед л гаргасны дараа | ~$4–5 (~15,000₮) |

AWS-ийн үнийг AWS Pricing Calculator-аар баталгаажуулах хэрэгтэй. 1$ ≈ 3,500₮ гэж тооцсон.

## Найдвартай байдлыг хангах хямд арга хэмжээ

- **EC2:** auto-recovery асаана (үнэгүй). Сервер нь төлөвгүй байх ёстой: Caddy-ийн тохиргоог DB-ээс дахин үүсгэдэг, user-data скриптээр серверийг шинээр босгож чаддаг байна.
- **Backup:** RDS автомат backup-ийг 7 хоног хадгална. EC2-ийн EBS-ийг DLM-ээр өдөр бүр snapshot хийнэ (~$1).
- **Мэдэгдэл:** CloudWatch alarm (EC2 status check, RDS CPU/storage) имэйлээр ирнэ.
- **Spot тасалдал:** ECS аппыг автоматаар дахин эхлүүлнэ. Төлбөртэй багцын аппыг дараа нь on-demand Fargate дээр ажиллуулна.
- **Сэргээх туршилт:** сар бүр backup-аас сэргээж туршина.

---

## Ажлын жагсаалт

### 0. 10/17-ноос өмнө — эзэмшигчийн бэлтгэл (2026-10-11: Render ашиглахгүй, бүгд AWS)
- [ ] GitHub-ийн private repo үүсгэж эхний commit-ийг push хийх
- [ ] Anthropic API түлхүүр авч, сарын зарцуулалтын хязгаар тавих (console.anthropic.com)
- [ ] AWS аккаунт (Paid plan), $50 төсвийн мэдэгдэл, root-д MFA (1-р хэсэг)
- [ ] AWS бүс сонгох (Tokyo / Seoul)
- [ ] EC2 t4g.small (Ubuntu 24.04 arm64), Elastic IP, security group 22 (өөрийн IP) / 80 / 443
- [ ] `aidolab.mn` DNS: `A app → Elastic IP`
- [ ] `deploy/README.md`-ийн дагуу суулгах; `/etc/aidolab/env`-д `ANTHROPIC_API_KEY`
- [ ] Багшийн superuser үүсгэх, CloudWatch alarm + EBS snapshot (DLM) тохируулах
- [ ] 1–2 сурагчийн бодит repo-гоор урьдчилан турших
- [ ] Хичээлд хэлэх зүйл: шалгалт бодит, deploy/DB/төлбөр одоохондоо демо
- [ ] ai-academy.asia дээр танилцуулга, `app.aidolab.mn` холбоос тавих (Activate өргөдөлд хэрэгтэй)

### 0б. Эзэмшигчийн шийдвэр (кодоос өмнө)
- [ ] Багцын үнэ, хязгаар (`checker/plans.py`: одоо 0₮ / 29,000₮ / 79,000₮ түр)
- [ ] AWS бүс: Tokyo эсвэл Seoul
- [ ] Infrastructure-as-code: Terraform эсвэл AWS CDK (Python)
- [ ] Mentor-ийн асуултад хэн, хэдэн цагийн дотор хариулах
- [ ] Хүүхэд хэрэглэгчид: нууцлалын бодлого, үйлчилгээний нөхцөл, эцэг эхийн зөвшөөрөл хэрэгтэй эсэх (хуульчаар шалгуулах)

### 0в. Урт хугацаа шаардах ажил — одооноос эхлүүлэх
- [ ] **QPay merchant гэрээ:** компанийн гэрчилгээ, дансны мэдээлэлтэй хүсэлт гаргах. Батлагдах хүртэл хэдэн долоо хоног болж магадгүй
- [ ] `aidolab.mn` бүртгэгч дээр NS-ийг Route 53 руу солих эрх байгаа эсэхийг асуух
- [ ] AWS аккаунт + Activate өргөдөл (доорх 1-р хэсэг)

### 1. AWS аккаунт ба credit
- [ ] AWS аккаунтыг **Paid plan**-аар нээх (компанийн имэйлээр, төлбөрийн карттай)
- [ ] **AWS Budgets:** сарын $50 төсөв тавьж, имэйлээр мэдэгдэл авдаг болгох (хамгийн түрүүнд хийх)
- [ ] Root аккаунтад MFA тохируулж, өдөр тутмын ажилд тусдаа IAM хэрэглэгч эсвэл Identity Center ашиглах
- [ ] **AWS Activate Founders**-т өргөдөл гаргах. Бэлдэх зүйлс:
  - [ ] AWS Account ID
  - [ ] Компанийн мэдээлэл (нэр, үүсгэн байгуулсан огноо, ажилчдын тоо, санхүүжилтийн шат)
  - [ ] Ажилладаг вэбсайт (бүтээгдэхүүний тайлбартай)
  - [ ] Англи тайлбар (жишээ нь доор)
- [ ] Монголын accelerator, incubator, хөрөнгө оруулагчдаас Activate Portfolio provider мөн эсэхийг асуух
- [ ] Credit Amazon Bedrock дээрх Claude-д хамаарах эсэхийг шалгах. Хамаардаг бол `ai.py`-г Bedrock руу шилжүүлэх
- [ ] Бүс сонгох: Tokyo (`ap-northeast-1`) эсвэл Seoul (`ap-northeast-2`). Монголоос хоцрогдол болон үнийг харьцуулна

Activate өргөдлийн тайлбарын жишээ:

> AI Academy Asia is an education company in Mongolia teaching adults and children to build software with AI. We are building a deployment platform for beginner developers: it scans a student's GitHub repository for leaked secrets, insecure configuration and vulnerable dependencies, explains the results in Mongolian using AI, and will host their Python apps on AWS with local-currency (MNT) billing. We plan to run student apps on Amazon ECS Fargate with Amazon RDS for PostgreSQL and Amazon S3.

### 2. Кодын бэлтгэл (AWS шаардлагагүй, одоо эхлэх боломжтой)
- [x] Хэрэглэгчийн бүртгэл ба нэвтрэлт (имэйл + нууц үг; 2026-10-17-ноос урилгын код хассан, бүртгэл нээлттэй). Багш `/admin/`-аар удирдана (2026-10-11)
- [ ] `DATABASE_URL`-аар Postgres дэмжих. Локал дээр SQLite хэвээр үлдэнэ
- [x] Deploy-ийг background worker-оор ажиллуулах (DB дээр суурилсан queue, `run_worker`). Шалгалт одоохондоо шууд (~3–60 сек)
- [x] `Service` загварыг өргөтгөх: subdomain (`slug`), орчны хувьсагч (`EnvVar`), шалгалтын түүх (`ScanRun`)
- [ ] Deploy төлөв, deploy түүх (hosting эхлэхэд)
- [ ] `EnvVar`-ын утга одоо DB-д энгийн текстээр хадгалагдаж байна. Hosting эхлэхэд SSM SecureString руу шилжүүлнэ
- [ ] Шинэ шалгалтууд (`Finding` хэлбэрээр):
  - [x] Файлыг локал дискэнд хадгалж байгаа эсэх (`open(..., "w")`, `MEDIA_ROOT`). "Redeploy хийхэд устна, S3 ашигла" гэж тайлбарлана
  - [x] Production-д SQLite ашиглаж байгаа эсэх
  - [ ] DB-ийн нууц үг кодонд бичигдсэн эсэх, `DATABASE_URL`-ийг уншиж байгаа эсэх
  - [x] Start command байгаа эсэх (Procfile эсвэл gunicorn тохиргоо)
- [ ] Deploy-ийн шалгуур: оноо 80-аас доош бол "Deploy хийх" товч идэвхгүй байна
- [x] Infrastructure-as-code: **Terraform** (2026-10-16)

### 2б. Демо горим (2026-10-11 хийгдсэн)
- [x] Бүх UI ажилладаг: deploy + log, Postgres, файлын сан, Mentor, QPay нэхэмжлэх (симуляц, `DEMO_MODE`)
- [ ] Hosting бодитоор эхлэхэд `checker/hosting.py`-ийн функцуудыг ECS/RDS/S3 дуудлагаар солих, QPay API холбох, `DEMO_MODE=0`
- [ ] `hosting.metrics()`-ийг CloudWatch (ECS Container Insights: CPU/санах ой) болон Caddy access log-оор (хүсэлт, p50/p95, урсгал) солих
- [ ] Багцын үнийг эцэслэх (`checker/plans.py`: Үнэгүй 0₮, Сурагч 29,000₮, Pro 79,000₮ — түр утга)

### 3. AWS дэд бүтэц
- [x] VPC: 2 AZ, public subnet (gateway), private subnet (апп, RDS) — `infra/network.tf`
- [ ] Security group:
  - [ ] Апп → RDS:5432 зөвхөн
  - [ ] Gateway → апп-ын порт
  - [ ] Апп → gateway-ийн admin руу хандах эрхгүй
- [ ] Gateway EC2 `t4g.small`:
  - [ ] Elastic IP
  - [ ] Auto-recovery
  - [ ] NAT (iptables masquerade, source/dest check унтраах)
  - [ ] User-data скриптээр дахин босгодог байх
- [ ] Caddy: маршрутыг DB-ээс үүсгэнэ (домэйн, SSL-ийг 3б хэсгээс)
- [x] Django dashboard-ийг gateway дээр systemd + Caddy-гаар ажиллуулах скриптүүд (`deploy/`, 2026-10-11)
- [x] Worker-ыг systemd service болгох (`deploy/aidolab-worker.service`)
- [x] RDS Postgres `db.t4g.micro`: private, backup-ийг 7 хоног хадгалах, нийтэд нээлттэй биш — `infra/data.tf`
- [x] S3 bucket: Block Public Access, апп бүрд prefix
- [x] ECR repository (нийт 300 image хүртэл; апп бүрийн сүүлийн 3-ыг worker цэвэрлэх — дараа)
- [x] CodeBuild project: VPC-гүй, зөвхөн ECR-д push хийх эрхтэй (`infra/buildspec.yml`)
- [x] ECS cluster: `FARGATE_SPOT` capacity provider
- [ ] IAM:
  - [ ] Worker role: ECS, CodeBuild, SSM, IAM-ийн хязгаарлагдмал эрх
  - [ ] Апп бүрийн task role: зөвхөн өөрийн S3 prefix
- [x] SSM Parameter Store (SecureString): апп бүрийн орчны хувьсагч
- [ ] CloudWatch Logs: 7 хоног хадгалах. Alarm: EC2 status, RDS CPU, RDS storage → имэйл
- [x] DLM: gateway EBS-ийн өдөр тутмын snapshot
- [ ] Route 53: домэйн болон wildcard бичлэг (дэлгэрэнгүйг 3б хэсгээс)

### 3б. Домэйн ба SSL — `aidolab.mn`
**Шийдвэр (2026-10-11):** апп бүр `aidolab.mn` дээр автоматаар дэд домэйн авна. Хэрэглэгч өөрийн домэйнээ холбож болох бөгөөд SSL-ийг бид автоматаар тохируулна.
Сурагчийн аппыг компанийн `ai-academy.asia` домэйнээс тусдаа байлгана (`onrender.com`, `github.io`-той адил зарчим).

| Юу | Хаяг | SSL |
|---|---|---|
| Сурагчийн апп (анхдагч) | `<апп-нэр>.aidolab.mn` | Wildcard `*.aidolab.mn`, Let's Encrypt DNS-01 (Route 53), Caddy автоматаар шинэчилнэ |
| Хэрэглэгчийн өөрийн домэйн | `www.shop.mn`, `shop.mn` | Caddy **on-demand TLS**: баталгаажсаны дараах эхний хүсэлтээр HTTP-01-ээр сертификат авна |
| Dashboard | **`app.aidolab.mn`** (шийдсэн) | Wildcard `*.aidolab.mn`-д хамрагдана |
| `aidolab.mn` (apex) | Танилцуулга хуудас | Let's Encrypt |

**Cookie-ийн хамгаалалт:** dashboard `app.aidolab.mn` дээр байрлах тул сурагчийн апп `.aidolab.mn` бүхэлд нь cookie тавьж чадна (cookie tossing). Үүнээс сэргийлж Django-ийн cookie-г `__Host-` угтвартай болгоно (`SESSION_COOKIE_NAME="__Host-sessionid"`, `CSRF_COOKIE_NAME="__Host-csrftoken"`). Ийм cookie-г дэд домэйн дарж бичиж чадахгүй. (`settings.py`-д хийгдсэн, production-д идэвхтэй.)

**Хэрэглэгчийн домэйн холбох урсгал:**
1. Dashboard → Домэйн нэмэх → `www.shop.mn`
2. Систем DNS-ийн заавар харуулна:
   - Дэд домэйн (`www`): `CNAME → <апп-нэр>.aidolab.mn`
   - Apex (`shop.mn`): `A → <Elastic IP>`. Apex дээр CNAME тавих боломжгүй. ALIAS/flattening дэмждэг DNS бол түүгээр холбож болно
   - Эзэмшлийн баталгаа: `TXT _aidolab.<домэйн> = <токен>`
3. Worker DNS-ийг 48 цагийн турш давтан шалгана: эхэндээ хэдэн минут тутам, дараа нь цөөрүүлнэ
4. Баталгаажиж, домэйн манай IP руу заамагц worker `/srv/aidolab/data/domains/wanted` файлд бичнэ → root-ийн `sync-domains.py` (systemd path unit) certbot-оор (HTTP-01, webroot) сертификат авч, nginx server block үүсгэнэ
5. Dashboard-д төлөв харуулна: "DNS хүлээж байна" → "Баталгаажсан" → "SSL идэвхтэй"
6. Домэйн устгахад жагсаалтаас хасагдаж, `sync-domains.py` nginx block болон сертификатыг устгана

TXT токен шаардах шалтгаан: өөр хүний DNS аль хэдийн манай IP руу заасан байсан ч (жишээ нь хуучин бичлэг үлдсэн) тэр домэйнийг булааж авах боломжгүй болгоно.

**Зардал:** `aidolab.mn` жилийн төлбөр (бүртгэгчээс хамаарна), Route 53 zone сард $0.50, SSL үнэгүй.

- [x] `aidolab.mn`-г эзэмшиж байгаа эсэхийг шалгах (эзэмшигчид бий)
- [ ] Бүртгэгч дээр NS солих боломжтой эсэхийг шалгах
- [ ] Route 53 hosted zone `aidolab.mn` үүсгэж, бүртгэгч дээр NS-ийг Route 53 руу заах
- [ ] DNS: `*.aidolab.mn` → Elastic IP (`app.aidolab.mn` dashboard-ийг оруулаад), `aidolab.mn` → Elastic IP (танилцуулга хуудас)
- [ ] Caddy-г `caddy-dns/route53` модультайгаар бүтээх (xcaddy). Gateway-ийн IAM role-д зөвхөн энэ zone-ийн эрх өгөх
- [ ] Caddy: `*.aidolab.mn` wildcard cert + on-demand TLS (`ask` endpoint-тэй) + HTTP→HTTPS + HSTS
- [x] Django: `__Host-` cookie (production-д)
- [ ] `ALLOWED_HOSTS=app.aidolab.mn` орчны хувьсагч тохируулах (`CSRF_TRUSTED_ORIGINS` түүнээс автоматаар үүснэ)
- [x] `CustomDomain` загвар: домэйн, апп, токен, төлөв, сүүлд шалгасан огноо
- [x] Dashboard: домэйн нэмэх/устгах, DNS-ийн заавар (хуулах товчтой), төлвийн шошго, "Шалгах" товч (dns.google DoH-оор TXT шалгана)
- [x] Worker: DNS-ийг автоматаар давтан шалгах (2 цаг 5 мин тутам, дараа нь 30 мин, 48 цагийн дараа failed). A/CNAME чиглүүлэлтийг шалгах (`checker/domains.py`)
- [x] Сертификат: nginx + certbot HTTP-01, зөвхөн verified домэйн (`deploy/sync-domains.py`, root-ийн хуулбар `/usr/local/lib/aidolab/`, 30 мин-аас өмнө дахин оролдохгүй)
- [ ] Хязгаар: сурагчийн багцад апп тутамд 1 домэйн, Pro багцад 5
- [ ] `www` ↔ apex чиглүүлэх сонголт
- [ ] Апп-ын нэрийн дүрэм:
  - [x] Зөвхөн `a-z 0-9 -` тэмдэгт, 3–40 тэмдэгт, давхцахгүй (`checker/naming.py`)
  - [x] Нөөцлөгдсөн нэр хориглох (`www`, `api`, `admin`, `app`, `deploy`, `mail`, `status` …)
  - [x] Банк, төрийн байгууллагын нэрийг хориглох (фишингээс сэргийлэх)
- [ ] Бүртгэлгүй дэд домэйн эсвэл домэйнээр хандвал "Апп олдсонгүй" гэсэн монгол 404 хуудас харуулах
- [ ] Хэрэглэгчид зориулсан монгол заавар: түгээмэл .mn бүртгэгчид дээр CNAME/A/TXT бичлэг нэмэх. Cloudflare ашигладаг бол proxy-г "DNS only" болгох
- [ ] (Pilot тогтворжсоны дараа) `aidolab.mn`-г Public Suffix List-д бүртгүүлэх

### 4. Deploy урсгал (worker)
- [ ] 1. Шалгагч ажиллана. Оноо 80-аас доош бол зогсоно
- [ ] 2. Эхний deploy бол DB + role үүсгэнэ (`CONNECTION LIMIT 5`, `statement_timeout`), S3 prefix болон IAM role үүсгэнэ
- [ ] 3. `DATABASE_URL`, S3 болон хэрэглэгчийн хувьсагчдыг SSM-д бичнэ
- [ ] 4. CodeBuild: Python аппад Dockerfile үүсгэнэ (requirements.txt, gunicorn эсвэл start command) → ECR
- [ ] 5. ECS service үүсгэх эсвэл шинэчлэх, health check
- [ ] 6. Caddy маршрут шинэчлэх → `https://<нэр>.aidolab.mn`
- [ ] 7. Build болон ажиллах үеийн log-ийг dashboard-д харуулах
- [ ] Rollback: өмнөх image руу буцаах товч
- [ ] Үйлчилгээ устгахад бүх нөөцийг (ECS, DB, S3 prefix, SSM, ECR) цэвэрлэх

### 5. Аюулгүй байдал
- [ ] Апп бүрд CPU/RAM хязгаар тавих. Хэт удаан өндөр CPU (mining) илрэхэд alarm өгч, аппыг зогсоох
- [ ] Апп нэг нэгнийхээ DB рүү хандах эрхгүй байх (role бүр зөвхөн өөрийн database-д)
- [ ] Сурагчийн код зөвхөн CodeBuild болон Fargate дээр ажиллана. Gateway дээр хэзээ ч ажиллахгүй (CLAUDE.md-ийн зарчим)
- [ ] Хүсэлтийн хязгаар (rate limit): шалгалт, deploy-ийн тоог хэрэглэгч тус бүрээр хязгаарлах

### 6. Зардал бууруулах (pilot эхэлсний дараа)
- [ ] Ашиглагдаагүй аппыг унтраах: 30 минут хүсэлтгүй бол ECS desired=0, Caddy хүсэлт ирэхэд асаана
- [ ] AI зөвлөгөөг сурагч товч дарах үед л гаргах
- [ ] ARM (Graviton) image-ээр Fargate ажиллуулах
- [ ] Pilot амжилттай болсны дараа Compute Savings Plan (1 жил) авах

### 7. Төлбөр
- [ ] QPay-ээр төгрөгөөр төлөх (webhook → эрх сунгах)
- [ ] Багцууд: сурагчийн (Spot, унтардаг) ба Pro (on-demand, тусдаа DB)
