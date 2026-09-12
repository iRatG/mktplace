from django.db import migrations

IT_TEAM_GROUP = "IT Team"


def create_it_team_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=IT_TEAM_GROUP)


def delete_it_team_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=IT_TEAM_GROUP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("business_queries", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_it_team_group, delete_it_team_group),
    ]
