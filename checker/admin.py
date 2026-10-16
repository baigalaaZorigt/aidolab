from django.contrib import admin
from django.utils import timezone

from .models import AuditEvent, ContactRequest, CustomDomain, Invoice, MentorRequest, Profile, ScanRun, Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "score", "updated_at")
    search_fields = ("name", "slug", "owner__email")
    # Findings/advice are large JSON; keep them read-only.
    readonly_fields = ("findings", "advice", "created_at", "updated_at")


@admin.register(ScanRun)
class ScanRunAdmin(admin.ModelAdmin):
    list_display = ("service", "score", "high", "medium", "low", "created_at")


@admin.register(CustomDomain)
class CustomDomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "service", "status", "last_checked_at")



@admin.register(MentorRequest)
class MentorRequestAdmin(admin.ModelAdmin):
    list_display = ("topic", "user", "service", "created_at", "answered_at")
    list_filter = ("answered_at",)
    readonly_fields = ("user", "service", "topic", "question", "created_at", "answered_at")
    fields = ("user", "service", "topic", "question", "answer", "created_at", "answered_at")

    def save_model(self, request, obj, form, change):
        if obj.answer and not obj.answered_at:
            obj.answered_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "plan_until")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "amount", "created_at", "paid_at")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "message", "service_name", "ip")
    list_filter = ("kind",)
    search_fields = ("message", "user__email", "service_name")

    # The log is append-only: teachers can read it but not edit or delete entries.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "contact", "topic", "link", "handled")
    list_filter = ("handled", "topic")
    list_editable = ("handled",)
    search_fields = ("contact", "link", "message")
    readonly_fields = ("contact", "link", "message", "topic", "ip", "created_at")
