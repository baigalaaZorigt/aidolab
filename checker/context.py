import math

from django.conf import settings
from django.utils import timezone

from .models import profile_for
from .oauth import enabled_providers
from .plans import get_plan


def workspace(request):
    context = {"DEMO_MODE": settings.DEMO_MODE, "OAUTH": enabled_providers()}
    if request.user.is_authenticated:
        profile = profile_for(request.user)
        context["plan"] = get_plan(profile.effective_plan)
        if profile.effective_plan == "free" and profile.free_until:
            left = (profile.free_until - timezone.now()).total_seconds() / 86400
            context["free_days_left"] = max(0, math.ceil(left))
    return context
