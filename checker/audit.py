from .models import AuditEvent


def audit(request, kind: str, message: str, service=None, user=None) -> None:
    """Record a workspace activity. Pass names only, never secret values."""
    AuditEvent.objects.create(
        user=user or request.user, service=service, service_name=service.name if service else "",
        kind=kind, message=message, ip=request.META.get("REMOTE_ADDR") or None,
    )
