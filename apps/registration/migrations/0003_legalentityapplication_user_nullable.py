import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('registration', '0002_registration_reviewers_group'),
    ]

    operations = [
        migrations.AlterField(
            model_name='legalentityapplication',
            name='user',
            field=models.ForeignKey(blank=True, help_text='Заполняется только при выдаче доступа — до этого аккаунта не существует', limit_choices_to={'role': 'advertiser'}, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='legal_entity_applications', to=settings.AUTH_USER_MODEL),
        ),
    ]
