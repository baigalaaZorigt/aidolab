"""Billing (simulated QPay) and mentor requests."""
import csv
import hashlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import hosting
from .audit import audit
from .models import AuditEvent, DedicatedIP, Invoice, MentorRequest, profile_for
from .plans import OLD_KEYS, PLAN_DAYS, PLANS, get_plan


@login_required
def billing(request):
    profile = profile_for(request.user)
    if request.method == "POST":
        key = request.POST.get("plan")
        if key not in PLANS:
            return redirect("billing")
        if key == "free":
            profile.plan, profile.plan_until = "free", None
            profile.save()
            messages.success(request, "Үнэгүй багц руу шилжлээ.")
            audit(request, "billing", "Үнэгүй багц руу шилжив")
            return redirect("billing")
        invoice = Invoice.objects.create(user=request.user, plan=key, amount=PLANS[key]["price"])
        return redirect("invoice", pk=invoice.pk)
    invoices = list(request.user.invoices.all()[:20])
    for inv in invoices:
        inv.plan_name = get_plan(inv.plan)["name"] if inv.plan in PLANS or inv.plan in OLD_KEYS else inv.plan
    return render(request, "checker/billing.html", {
        "plans": [get_plan(k) for k in PLANS], "profile": profile,
        "current": get_plan(profile.effective_plan)["key"], "invoices": invoices,
    })


def qr_cells(seed: str, size: int = 25) -> list[tuple[int, int]]:
    """Decorative QR-like pattern (not a scannable code) for the simulated invoice."""
    bits = bin(int(hashlib.sha256(seed.encode()).hexdigest() * 3, 16))[2:]
    cells = []
    for y in range(size):
        for x in range(size):
            finder = any(x0 <= x < x0 + 7 and y0 <= y < y0 + 7 for x0, y0 in ((0, 0), (size - 7, 0), (0, size - 7)))
            if finder:
                fx, fy = x % (size - 7) if x >= size - 7 else x, y % (size - 7) if y >= size - 7 else y
                if fx in (0, 6) or fy in (0, 6) or (2 <= fx <= 4 and 2 <= fy <= 4):
                    cells.append((x, y))
            elif bits[(y * size + x) % len(bits)] == "1":
                cells.append((x, y))
    return cells


@login_required
def invoice(request, pk):
    inv = get_object_or_404(Invoice, pk=pk, user=request.user)
    if request.method == "POST" and inv.paid_at is None:
        inv.paid_at = timezone.now()
        inv.save(update_fields=["paid_at"])
        profile = profile_for(request.user)
        start = max(profile.plan_until or timezone.now(), timezone.now()) if profile.plan == inv.plan else timezone.now()
        profile.plan, profile.plan_until = inv.plan, start + timezone.timedelta(days=PLAN_DAYS)
        profile.save()
        messages.success(request, f"Төлбөр амжилттай. {PLANS[inv.plan]['name']} багц идэвхжлээ.")
        audit(request, "billing", f"{inv.number} төлөгдөж {PLANS[inv.plan]['name']} багц идэвхжив ({inv.amount_label})")
        return redirect("billing")
    return render(request, "checker/invoice.html", {
        "inv": inv, "plan": get_plan(inv.plan), "cells": qr_cells(inv.number), "plan_info": get_plan(inv.plan)})


@login_required
def mentor(request):
    if request.method == "POST":
        topic = request.POST.get("topic", "").strip()
        question = request.POST.get("question", "").strip()
        service = request.user.services.filter(pk=request.POST.get("service") or 0).first()
        if not topic or not question:
            messages.error(request, "Сэдэв болон асуултаа бичнэ үү.")
        else:
            MentorRequest.objects.create(user=request.user, service=service, topic=topic, question=question)
            audit(request, "mentor", f"Mentor-оос асуув: {topic}", service)
            messages.success(request, "Асуулт илгээгдлээ. Mentor хариулахад энд харагдана.")
        return redirect("mentor")
    return render(request, "checker/mentor.html", {
        "requests": request.user.mentor_requests.select_related("service"),
        "services": request.user.services.all(),
        "selected": request.GET.get("service", ""),
    })


@login_required
def system_log(request):
    events = request.user.audit_events.all()
    kind = request.GET.get("kind", "")
    service = request.GET.get("service", "")
    q = request.GET.get("q", "").strip()
    if kind in AuditEvent.KINDS:
        events = events.filter(kind=kind)
    if service:
        events = events.filter(service_name=service)
    if q:
        events = events.filter(message__icontains=q)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="aidolab-system-log.csv"'
        response.write("﻿")  # BOM so Excel reads Cyrillic correctly
        writer = csv.writer(response)
        writer.writerow(["Огноо", "Төрөл", "Үйлдэл", "Апп", "IP"])
        for e in events[:5000]:
            writer.writerow([timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                             e.kind_label, e.message, e.service_name, e.ip or ""])
        return response
    names = request.user.audit_events.exclude(service_name="").values_list("service_name", flat=True).distinct()
    return render(request, "checker/system_log.html", {
        "events": events[:200], "kinds": AuditEvent.KINDS.items(), "service_names": sorted(set(names)),
        "kind": kind, "service": service, "q": q,
    })


@login_required
def dedicated_ip(request):
    plan = get_plan(profile_for(request.user).effective_plan)
    current = DedicatedIP.objects.filter(user=request.user).first()
    services = request.user.services.all()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "enable" and current is None:
            if not plan["dedicated_ip"]:
                messages.error(request, "Dedicated IP нь Pro багцад багтдаг.")
            else:
                current = DedicatedIP.objects.create(user=request.user, address=hosting.allocate_ip(request.user))
                audit(request, "network", f"Dedicated IP {current.address} авлаа")
                messages.success(request, f"Dedicated IP {current.address} идэвхжлээ. Ашиглах апп-уудаа сонгоно уу.")
        elif action == "assign" and current:
            chosen = set(request.POST.getlist("services"))
            for s in services:
                use = str(s.pk) in chosen
                if s.use_dedicated_ip != use:
                    s.use_dedicated_ip = use
                    s.save(update_fields=["use_dedicated_ip"])
                    audit(request, "network", f"Гарах IP: {current.address if use else 'хуваалцсан IP'}", s)
            messages.success(request, "Хадгалагдлаа. Дараагийн deploy-оос хэрэгжинэ.")
        elif action == "release" and current:
            audit(request, "network", f"Dedicated IP {current.address} цуцаллаа")
            services.update(use_dedicated_ip=False)
            current.delete()
            messages.success(request, "Dedicated IP цуцлагдлаа.")
        return redirect("dedicated_ip")
    return render(request, "checker/dedicated_ip.html", {
        "current": current, "services": services, "shared_ip": hosting.gateway_ip(), "plan_allows": plan["dedicated_ip"],
    })
