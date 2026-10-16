from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the deploy worker (HOSTING_BACKEND=aws)."

    def handle(self, *args, **options):
        if settings.HOSTING_BACKEND != "aws":
            raise CommandError("HOSTING_BACKEND=aws үед л ажиллана.")
        from checker.worker import loop
        loop()
