import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LegalEntityApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_name', models.CharField(max_length=255)),
                ('inn', models.CharField(max_length=20, verbose_name='ИНН')),
                ('status', models.CharField(choices=[('pending', 'На проверке'), ('approved', 'Подтверждена'), ('rejected', 'Отклонена')], default='pending', max_length=20)),
                ('rejection_reason', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('ddocs_status', models.CharField(choices=[('not_sent', 'Договор не отправлен'), ('contract_sent', 'Договор отправлен'), ('signed', 'Договор подписан'), ('access_issued', 'Доступ выдан')], default='not_sent', max_length=20)),
                ('ddocs_note', models.TextField(blank=True, help_text='Свободный комментарий сотрудника о переписке в Ддокс')),
                ('retention_anchor_at', models.DateTimeField(blank=True, help_text='Дата финального статуса заявки. От неё отсчитываются 3 года хранения.', null=True)),
                ('is_frozen', models.BooleanField(default=False, help_text='Заморожено (например активное разбирательство) — удаление запрещено.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_to', models.ForeignKey(blank=True, help_text='Сотрудник, за которым закреплена заявка (назначается автоматически при подаче)', limit_choices_to={'is_staff': True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_legal_entity_applications', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_legal_entity_applications', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(limit_choices_to={'role': 'advertiser'}, on_delete=django.db.models.deletion.CASCADE, related_name='legal_entity_applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Заявка юрлица на регистрацию',
                'verbose_name_plural': 'Заявки юрлиц на регистрацию',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LegalEntityApplicationStatusLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_status', models.CharField(blank=True, max_length=20)),
                ('new_status', models.CharField(max_length=20)),
                ('comment', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('application', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_logs', to='registration.legalentityapplication')),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='legal_entity_status_changes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Смена статуса заявки юрлица',
                'verbose_name_plural': 'Смены статуса заявок юрлиц',
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='IdentityVerification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(max_length=255)),
                ('phone', models.CharField(max_length=30)),
                ('pinfl', models.CharField(max_length=14, verbose_name='ПИНФЛ')),
                ('status', models.CharField(choices=[('pending', 'В обработке'), ('verified', 'Подтверждён'), ('failed', 'Не подтверждён')], default='pending', max_length=20)),
                ('provider', models.CharField(default='oneid_stub', max_length=30)),
                ('provider_reference', models.CharField(blank=True, max_length=100)),
                ('failure_reason', models.TextField(blank=True)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('retention_anchor_at', models.DateTimeField(blank=True, null=True)),
                ('is_frozen', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(blank=True, help_text='Заполняется только после успешной верификации — аккаунт блогера создаётся именно тогда', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='identity_verifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Подтверждение личности (OneID)',
                'verbose_name_plural': 'Подтверждения личности (OneID)',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IPApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(choices=[('patent', 'Патент'), ('spravka', 'Справка')], max_length=20)),
                ('document_number', models.CharField(blank=True, max_length=100)),
                ('file', models.FileField(help_text='PDF, JPG или PNG', upload_to='ip_documents/%Y/%m/')),
                ('status', models.CharField(choices=[('pending', 'На проверке'), ('approved', 'Подтверждена'), ('rejected', 'Отклонена')], default='pending', max_length=20)),
                ('rejection_reason', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('retention_anchor_at', models.DateTimeField(blank=True, null=True)),
                ('is_frozen', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('identity_verification', models.ForeignKey(blank=True, help_text='Должна быть VERIFIED перед подачей заявки на ИП', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ip_applications', to='registration.identityverification')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_ip_applications', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(limit_choices_to={'role': 'blogger'}, on_delete=django.db.models.deletion.CASCADE, related_name='ip_applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Заявка блогера на статус ИП',
                'verbose_name_plural': 'Заявки блогеров на статус ИП',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IPApplicationStatusLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_status', models.CharField(blank=True, max_length=20)),
                ('new_status', models.CharField(max_length=20)),
                ('comment', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('application', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_logs', to='registration.ipapplication')),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ip_application_status_changes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Смена статуса заявки на ИП',
                'verbose_name_plural': 'Смены статуса заявок на ИП',
                'ordering': ['created_at'],
            },
        ),
    ]
