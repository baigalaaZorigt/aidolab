"""Sign up, log in, account settings. Users log in with email (stored as username)."""
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .audit import audit
from .models import SocialAccount

MIN_PASSWORD = 8


def password_error(password: str, email: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Нууц үг хамгийн багадаа {MIN_PASSWORD} тэмдэгттэй байна."
    if password.lower() in email.lower() or password.isdigit():
        return "Нууц үг хэт энгийн байна. Үсэг, тоо холино уу."
    return None


def safe_next(request) -> str:
    target = request.POST.get("next") or request.GET.get("next") or "/dashboard/"
    return target if url_has_allowed_host_and_scheme(target, {request.get_host()}) else "/dashboard/"


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    context = {"next": request.GET.get("next", "")}
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        user = authenticate(request, username=email, password=request.POST.get("password", ""))
        if user is None:
            context.update(error="Имэйл эсвэл нууц үг буруу байна.", email=email, next=request.POST.get("next", ""))
            return render(request, "checker/login.html", context)
        login(request, user)
        audit(request, "auth", "Нэвтэрлээ")
        return redirect(safe_next(request))
    return render(request, "checker/login.html", context)


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    context = {}
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        context.update(name=name, email=email)
        error = None
        if not name:
            error = "Нэрээ оруулна уу."
        else:
            try:
                validate_email(email)
            except ValidationError:
                error = "Имэйл хаяг буруу байна."
        error = error or password_error(password, email)
        if not error and User.objects.filter(username=email).exists():
            error = "Энэ имэйлээр бүртгэл аль хэдийн үүссэн байна. Нэвтэрнэ үү."
        if error:
            context["error"] = error
            return render(request, "checker/signup.html", context)
        user = User.objects.create_user(username=email, email=email, password=password, first_name=name)
        login(request, user)
        audit(request, "auth", "Бүртгэл үүсгэв")
        messages.success(request, f"Тавтай морил, {name}! Эхний repo-гоо шалгуулаарай.")
        return redirect("new")
    return render(request, "checker/signup.html", context)


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        audit(request, "auth", "Гарлаа")
    logout(request)
    return redirect("login")


@login_required
def account_view(request):
    user = request.user
    if request.method == "POST":
        if request.POST.get("action") == "profile":
            name = request.POST.get("name", "").strip()
            if name:
                user.first_name = name
                user.save(update_fields=["first_name"])
                messages.success(request, "Мэдээлэл хадгалагдлаа.")
            else:
                messages.error(request, "Нэрээ оруулна уу.")
        else:
            new = request.POST.get("new_password", "")
            # Users who signed up with GitHub/Google have no password yet; let them set one.
            if user.has_usable_password() and not user.check_password(request.POST.get("old_password", "")):
                messages.error(request, "Одоогийн нууц үг буруу байна.")
            elif error := password_error(new, user.email):
                messages.error(request, error)
            else:
                user.set_password(new)
                user.save()
                update_session_auth_hash(request, user)
                audit(request, "auth", "Нууц үг солив")
                messages.success(request, "Нууц үг солигдлоо.")
        return redirect("account")
    from .oauth import enabled  # oauth imports this module, so import lazily
    linked = {a.provider: a for a in user.social_accounts.all()}
    providers = [{"key": k, "label": label, "account": linked.get(k), "enabled": enabled(k)}
                 for k, label in SocialAccount.PROVIDERS.items()]
    return render(request, "checker/account.html", {"tab": "account", "providers": providers})
