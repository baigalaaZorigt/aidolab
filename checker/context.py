from django.conf import settings

from .models import profile_for
from .plans import get_plan


def workspace(request):
    context = {"DEMO_MODE": settings.DEMO_MODE}
    if request.user.is_authenticated:
        context["plan"] = get_plan(profile_for(request.user).effective_plan)
    return context
