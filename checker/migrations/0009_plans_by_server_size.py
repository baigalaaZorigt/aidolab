from django.db import migrations


def student_to_standard(apps, schema_editor):
    # The old "student" plan (3 apps) became "standard" when plans moved to server sizes.
    apps.get_model("checker", "Profile").objects.filter(plan="student").update(plan="standard")
    apps.get_model("checker", "Invoice").objects.filter(plan="student").update(plan="standard")


class Migration(migrations.Migration):
    dependencies = [("checker", "0008_social_account")]
    operations = [migrations.RunPython(student_to_standard, migrations.RunPython.noop)]
