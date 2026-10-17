"""Public landing page: home, one page per help service, and the contact form."""

from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import ContactRequest
from .plans import PLANS, get_plan

SERVICES = [
    {
        "slug": "deploy", "topic": "Hosting", "visual": "deploy",
        "name": "Онлайн болгох", "category": "Deploy & Hosting",
        "title": "Онлайн болгох (Deploy & Hosting)",
        "short": "Төслийг тань интернетэд байршуулж, тогтвортой, аюулгүй ажиллагааг хангана. "
                 "Домэйн нэр, SSL сертификат тохируулна. Эхний 14 хоног үнэгүй.",
        "lead": "Таны төслийг интернетэд найдвартай байршуулж, 24/7 тасралтгүй, тогтвортой ажиллагааг хангана.",
        "helps": [
            "ChatGPT эсвэл Claude-аас авсан кодоо интернетэд хэрхэн байршуулахаа мэдэхгүй байгаа үед",
            "Domain (домэйн нэр) болон SSL (аюулгүй байдлын сертификат) холбоход хүндрэл гарсан үед",
            "AWS, Vercel, Render эсвэл VPS сервер тохируулж чадахгүй гацсан үед",
            "Өгөгдлийн санг (Database) сервертэй холбоход алдаа заагаад байгаа үед",
        ],
        "steps": [
            "Таны төслийн файлыг хүлээн авч, серверийн орчинг бэлтгэнэ.",
            "Домэйн нэр холбож, SSL (HTTPS) сертификатыг үнэгүй тохируулна.",
            "Өгөгдлийн санг серверийн орчинд аюулгүй холбоно.",
            "Серверийн ажиллагааг тохируулж, вэб сайтыг бүрэн онлайн болгоно.",
        ],
        "price": "14 хоног ₮0", "price_note": "hosting",  # filled from plans.py
        "time": "24 – 48 цаг", "time_note": "Бэлэн файлууд хүлээн авснаас хойш",
        "note_icon": "🔒",
        "note": "Таны эх код болон өгөгдлийн сан зөвхөн таны эзэмшилд байна. "
                "Та хүссэн үедээ эх кодоо бүрэн татан авах боломжтой.",
        "cta": "Энэ үйлчилгээг авах",
    },
    {
        "slug": "rescue", "topic": "Rescue & Fixes", "visual": "rescue",
        "name": "Код засвар, алдаа арилгах", "category": "Rescue & Fixes",
        "title": "Код засвар & Алдаа арилгах (Rescue & Fixes)",
        "short": "AI-ийн гаргасан бүтэцгүй алгоритм, систем дэх кодны алдаа болон аюулгүй байдлын "
                 "цоорхойг мэргэжлийн түвшинд цэгцлэн засна.",
        "lead": "AI-ийн гаргасан ойлгомжгүй алдаа, логикийн гацаа болон аюулгүй байдлын цоорхойг "
                "мэргэжлийн хөгжүүлэгчид засна.",
        "helps": [
            "ChatGPT эсвэл Claude-д дахин дахин хүсэлт өгсөн ч нэг алдаагаа засаж чадахгүй эргэлдээд байгаа үед",
            "Кодны бүтэц эвдэрч, ямар ч функц нь ажиллахаа больсон үед",
            "Вэб сайт тань хэт удаан ажиллаж, гацаж байгаа үед",
            "Нууц үг, дата алдагдахаас сэргийлж аюулгүй байдлыг хангах шаардлагатай үед",
        ],
        "steps": [
            "Кодонд үнэгүй оношилгоо хийж, алдааны эх үүсвэрийг тодорхойлно.",
            "AI-ийн бичсэн замбараагүй кодыг цэгцэлж, стандартын дагуу засна.",
            "Системийн аюулгүй байдлын цоорхойг нөхнө.",
            "Зассан кодоо шалгаж, бүрэн ажиллагааг танд хүлээлгэн өгнө.",
        ],
        "price": "Тохиролцоогоор", "price_note": "Үнэгүй оношилгооны дараа үнэлнэ",
        "time": "1 – 3 өдөр", "time_note": "Асуудлаас хамаарч",
        "note_icon": "🛡️",
        "note": "Бид таны аппликейшны хэвийн ажиллаж байгаа бусад хэсгийг огт хөндөхгүй бөгөөд "
                "зөвхөн алдаатай хэсгийг тусгаарлан засна.",
        "cta": "Кодоо үнэгүй шалгуулах",
    },
    {
        "slug": "finish", "topic": "Finishing", "visual": "finish",
        "name": "Гүйцээн дуусгах", "category": "Finishing & Features",
        "title": "Гүйцээн дуусгах (Finishing & Features)",
        "short": "Дутуу үлдсэн боломжууд (feature), өгөгдлийн сангийн холболт, нэвтрэх систем болон "
                 "QPay, SocialPay төлбөрийн хэрэгслийг холбоно.",
        "lead": "Хагас дутуу хийгдсэн функцууд, өгөгдлийн сан, нэвтрэх систем болон төлбөрийг холбоно.",
        "helps": [
            "Хэрэглэгч нэвтрэх (Login / Signup, Google Auth) хэсэг дутуу үед",
            "И-мэйл мэдэгдэл явуулах эсвэл файлууд серверт хуулах үйлдэл ажиллахгүй байх үед",
            "Дотоодын болон гадаадын төлбөрийн систем (QPay, SocialPay, Stripe) холбох шаардлагатай үед",
            "AI-аар дизайнаа хийлгэсэн ч цаанаа гараас гараагүй дутуу зүйлс олон байгаа үед",
        ],
        "steps": [
            "Төсөлд ямар дутуу боломжууд (features) байгааг хамтдаа жагсаана.",
            "Шаардлагатай Database болон API холболтуудыг хийж гүйцэтгэнэ.",
            "Нэвтрэх болон төлбөрийн системийг аюулгүй байдлын өндөр түвшинд тохируулна.",
            "Бүрэн ажиллагаатай болсны дараа шалгаж хүлээлгэн өгнө.",
        ],
        "price": "Төсөл тус бүрээр", "price_note": "Ажлын далайцаас хамаарна",
        "time": "2 – 5 өдөр", "time_note": "Ажлын хэмжээнээс хамаарч",
        "note_icon": "👁️",
        "note": "Хийгдэж буй нэмэлт ажил бүрийг бид монгол хэлээр энгийнээр тайлбарлаж, явцыг ил тод мэдээлнэ.",
        "cta": "Дутуу ажлаа үнэлүүлэх",
    },
    {
        "slug": "consulting", "topic": "Consulting", "visual": "consult",
        "name": "Зөвлөх үйлчилгээ", "category": "Consulting",
        "title": "Зөвлөх үйлчилгээ (Consulting)",
        "short": "Төсөл тань юунаас болж гацсаныг оношилж, цааш хэрхэн зөв, бага зардлаар хөгжүүлэх "
                 "гаргалгааг монгол хэлээр тайлбарлан чиглүүлнэ.",
        "lead": "Төсөл чинь яг юунд гацсаныг, цааш хэрхэн зөв хөгжүүлэх болон зардал хэмнэх талаар "
                "мэргэжлийн чиглүүлэг авна.",
        "helps": [
            "Төслөө цааш хэрхэн үргэлжлүүлэх талаар техникийн шийдвэр гаргаж чадахгүй байгаа үед",
            "Ямар сервис, сервер, датабэйс сонговол хамгийн бага зардалтайн тухай мэдээлэл хэрэгтэй үед",
            "ChatGPT эсвэл Claude зэрэг AI хэрэгслүүдийг хэрхэн зөв ашиглаж, кодоо чанартай бичүүлэхийг сурмаар байгаа үед",
            "Код засуулах биш, зүгээр л учрыг нь олоод өөрөө цааш хийхийг хүсэж байгаа үед",
        ],
        "steps": [
            "1-on-1 ганцаарчилсан онлайн уулзалт тохирно.",
            "Таны төслийн бүтэц, кодод дүн шинжилгээ хийнэ.",
            "Асуудлын шалтгааныг оношилж, алхам алхмаар засах гаргалгааг зааж өгнө.",
            "Цаашид анхаарах зүйлс болон архитектурын зөвлөмжийг бэлтгэн өгнө.",
        ],
        "price": "Тогтмол тариф", "price_note": "Нэг удаагийн ганцаарчилсан уулзалт",
        "time": "45 – 60 минут", "time_note": "Онлайн уулзалт",
        "note_icon": "💰",
        "note": "Бидний зөвлөгөө танд хэрэггүй гэж үзвэл эсвэл шийдэл гаргаж чадаагүй бол төлбөрийг 100% буцаан олгоно.",
        "cta": "Зөвлөгөөний цаг авах",
    },
]
TOPICS = {s["topic"] for s in SERVICES}


def _context(request, **extra) -> dict:
    starter_price = get_plan("starter")["price_label"]
    hosting = f"Үнэгүй багцаас эхэлнэ, Starter сард {starter_price}"
    services = [{**s, "num": f"{i:02d}", "price_note": hosting if s["price_note"] == "hosting" else s["price_note"]}
                for i, s in enumerate(SERVICES, 1)]
    return {"services": services, "hosting_note": hosting, "plans": [get_plan(k) for k in PLANS], "sent": request.GET.get("sent") == "1", **extra}


def home(request):
    return render(request, "checker/landing/home.html", _context(request))


def service_page(request, slug):
    ctx = _context(request)
    service = next((s for s in ctx["services"] if s["slug"] == slug), None)
    if service is None:
        raise Http404
    return render(request, "checker/landing/service.html", {**ctx, "service": service})


def privacy(request):
    return render(request, "checker/landing/privacy.html", _context(request))


def terms(request):
    return render(request, "checker/landing/terms.html", _context(request))


@require_POST
def contact(request):
    back = request.POST.get("back", "/")
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    contact_text = request.POST.get("contact", "").strip()[:200]
    # "website" is a hidden honeypot field: people leave it empty, bots fill it.
    if contact_text and not request.POST.get("website"):
        topic = request.POST.get("topic", "")
        ContactRequest.objects.create(
            contact=contact_text,
            link=request.POST.get("link", "").strip()[:500],
            message=request.POST.get("message", "").strip()[:3000],
            topic=topic if topic in TOPICS else "",
            ip=request.META.get("REMOTE_ADDR") or None,
        )
    return redirect(f"{back}?sent=1#contact")
