from django.db import migrations

REGISTRATION_REVIEWERS_GROUP = "Регистрация юрлиц"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=REGISTRATION_REVIEWERS_GROUP)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=REGISTRATION_REVIEWERS_GROUP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("registration", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
