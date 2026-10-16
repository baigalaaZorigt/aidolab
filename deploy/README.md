# aidolab-ийг AWS дээр байршуулах (gateway EC2)

`app.aidolab.mn` dashboard-ийг нэг жижиг EC2 дээр ажиллуулна: nginx + certbot (HTTPS, Let's Encrypt) → gunicorn → Django, өгөгдөл нь SQLite (EBS дээр, шөнө бүр backup).
Сурагчийн апп-уудыг (Fargate, RDS, S3) дараагийн шатанд нэмнэ — `HOSTING_PLAN.md`.

Ойролцоо зардал (Tokyo/Seoul): t4g.small ~$16 + Elastic IP ~$3.7 + 20GB gp3 ~$2 ≈ **~$22/сар** (+ Anthropic API).

## 1. AWS консол дээр (эзэмшигч)

1. **Бүс** сонгох (Tokyo `ap-northeast-1` эсвэл Seoul `ap-northeast-2`) — бүх нөөцийг нэг бүсэд үүсгэнэ.
2. **EC2 → Launch instance**
   - AMI: **Ubuntu Server 24.04 LTS (arm64)** (amd64 ч ажиллана)
   - Type: **t4g.small**
   - Key pair: шинээр үүсгэж `.pem`-ээ хадгална
   - Storage: **20 GB gp3**
   - Security group: `22` (SSH) — **зөвхөн өөрийн IP**, `80` ба `443` — бүх хүнд
3. **Elastic IP** → Allocate → энэ instance-д Associate.
4. **DNS:** Route 53-ийн `aidolab.mn` zone дээр `A  app  →  <Elastic IP>` бичлэг нэмнэ. ELB хэрэггүй — TLS-ийг nginx + certbot шийднэ.
5. **EC2 → Instance → Actions → Monitor → Manage CloudWatch alarms:** status check failed → имэйл, мөн *Recover instance* үйлдэл.
6. **EBS snapshot:** EC2 → Lifecycle Manager → өдөр бүр snapshot, 7 хоног хадгална.

## 2. Сервер дээр

```bash
# Өөрийн компьютер дээрээс
scp -i key.pem deploy/bootstrap.sh ubuntu@<Elastic IP>:~
ssh -i key.pem ubuntu@<Elastic IP>

# Сервер дээр
sudo REPO_URL=https://github.com/baigalaaZorigt/aidolab.git bash bootstrap.sh
#   → суулгаад certbot-оор app.aidolab.mn-ийн гэрчилгээ авна (DNS энэ IP руу заасан байх ёстой)
#   (Private repo бол git@github.com:... URL өгнө → deploy key хэвлэнэ → Deploy keys-д нэмээд дахин ажиллуулна)
sudo nano /etc/aidolab/env        # ANTHROPIC_API_KEY, ACCESS_CODE бөглөнө
sudo bash /srv/aidolab/app/deploy/update.sh
```

Багшийн бүртгэл:

```bash
cd /srv/aidolab/app && sudo -u aidolab env $(grep -v '^#' /etc/aidolab/env | xargs) \
  /srv/aidolab/venv/bin/python manage.py createsuperuser
```

`https://app.aidolab.mn` нээгдвэл бэлэн.

## 3. Шинэ код гаргах

GitHub руу push хийсний дараа сервер дээр: `sudo bash /srv/aidolab/app/deploy/update.sh`
(git pull → pip install → migrate → collectstatic → check --deploy → restart).

## Файлууд

| Файл | Үүрэг |
|---|---|
| `bootstrap.sh` | Анхны суулгалт: nginx, certbot, Python, `aidolab` хэрэглэгч, systemd, HTTPS гэрчилгээ |
| `update.sh` | Шинэ код гаргах |
| `aidolab.service` | gunicorn (127.0.0.1:8000, timeout 180) |
| `nginx-aidolab.conf` | `/static/` + reverse proxy (TLS хэсгийг certbot нэмнэ, `certbot.timer` сунгана) |
| `backup.sh`, `aidolab-backup.*` | Шөнө 03:30-д SQLite-ийн хуулбар, 14 хоног хадгална |
| `env.example` | `/etc/aidolab/env`-ийн загвар (нууц утга repo-д орохгүй) |

## 4. Бодит hosting (сурагчийн апп, `infra/` Terraform)

Шинэ gateway нь aidolab-ийн өөрийн VPC дотор байна (NAT + nginx + dashboard + worker). Хуучин gateway `cutover` хүртэл ажилласаар байна.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # admin_cidrs, alert_email
terraform init && terraform plan -out tfplan && terraform apply tfplan
terraform output -raw gateway_env               # → шинэ gateway-ийн /etc/aidolab/env-д нэмнэ
```

Шинэ gateway дээр: `bootstrap.sh` (дээрх 2-р хэсэг) → `gateway_env`-ийг `/etc/aidolab/env`-д нэмэх → `sudo bash /srv/aidolab/app/deploy/bootstrap-hosting.sh` (wildcard SSL, `*.aidolab.mn` proxy, worker).
Хуучин gateway-ээс SQLite-ийг хуулж аваад `cutover = true` → `terraform apply` (`app.aidolab.mn` шинэ IP руу заана). Дараа нь хуучин instance-ийг зогсооно.

| Файл | Үүрэг |
|---|---|
| `nginx-apps.conf` | `<slug>.aidolab.mn` → `<slug>.apps.aidolab.internal:8000` (Cloud Map, апп бүрд тохиргоо хэрэггүй) |
| `aidolab-worker.service` | `manage.py run_worker`: CodeBuild → ECS deploy, устгах ажлууд |
| `bootstrap-hosting.sh` | wildcard гэрчилгээ (certbot-dns-route53), apps proxy, worker асаах |
