# aidolab-ийг AWS дээр байршуулах (gateway EC2)

`app.aidolab.mn` dashboard-ийг нэг жижиг EC2 дээр ажиллуулна: Caddy (HTTPS, Let's Encrypt) → gunicorn → Django, өгөгдөл нь SQLite (EBS дээр, шөнө бүр backup).
Сурагчийн апп-уудыг (Fargate, RDS, S3) дараагийн шатанд нэмнэ — `HOSTING_PLAN.md`.

Ойролцоо зардал (Tokyo/Seoul): t4g.small ~$16 + Elastic IP ~$3.7 + 20GB gp3 ~$2 ≈ **~$22/сар** (+ Anthropic API).

## 1. AWS консол дээр (эзэмшигч)

1. **Бүс** сонгох (Tokyo `ap-northeast-1` эсвэл Seoul `ap-northeast-2`) — бүх нөөцийг нэг бүсэд үүсгэнэ.
2. **EC2 → Launch instance**
   - AMI: **Ubuntu Server 24.04 LTS (arm64)**
   - Type: **t4g.small**
   - Key pair: шинээр үүсгэж `.pem`-ээ хадгална
   - Storage: **20 GB gp3**
   - Security group: `22` (SSH) — **зөвхөн өөрийн IP**, `80` ба `443` — бүх хүнд
3. **Elastic IP** → Allocate → энэ instance-д Associate.
4. **DNS:** `aidolab.mn`-ийн одоогийн DNS удирдлага дээр `A  app  →  <Elastic IP>` бичлэг нэмнэ.
   (Wildcard `*.aidolab.mn` хэрэгтэй болоход Route 53 руу шилжинэ.)
5. **EC2 → Instance → Actions → Monitor → Manage CloudWatch alarms:** status check failed → имэйл, мөн *Recover instance* үйлдэл.
6. **EBS snapshot:** EC2 → Lifecycle Manager → өдөр бүр snapshot, 7 хоног хадгална.

## 2. Сервер дээр

```bash
# Өөрийн компьютер дээрээс
scp -i key.pem deploy/bootstrap.sh ubuntu@<Elastic IP>:~
ssh -i key.pem ubuntu@<Elastic IP>

# Сервер дээр
sudo REPO_URL=git@github.com:<org>/<repo>.git bash bootstrap.sh
#   → GitHub deploy key хэвлэнэ: repo → Settings → Deploy keys → Add (Read-only)
sudo REPO_URL=git@github.com:<org>/<repo>.git bash bootstrap.sh   # дахин
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
| `bootstrap.sh` | Анхны суулгалт: Caddy, Python, `aidolab` хэрэглэгч, deploy key, systemd |
| `update.sh` | Шинэ код гаргах |
| `aidolab.service` | gunicorn (127.0.0.1:8000, timeout 180) |
| `Caddyfile` | HTTPS + `/static/` + reverse proxy |
| `backup.sh`, `aidolab-backup.*` | Шөнө 03:30-д SQLite-ийн хуулбар, 14 хоног хадгална |
| `env.example` | `/etc/aidolab/env`-ийн загвар (нууц утга repo-д орохгүй) |
