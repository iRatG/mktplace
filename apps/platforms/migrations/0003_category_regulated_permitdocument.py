import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('platforms', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='is_regulated',
            field=models.BooleanField(
                default=False,
                help_text='Деятельность требует лицензии/разрешения по Приложению №1 к Закону РУз № ЗРУ-701 от 14.07.2021',
            ),
        ),
        migrations.AddField(
            model_name='category',
            name='regulated_doc_hint',
            field=models.TextField(
                blank=True,
                help_text='Описание документов, необходимых для данной регулируемой категории',
            ),
        ),
        migrations.CreateModel(
            name='PermitDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('doc_type', models.CharField(
                    choices=[
                        ('license', 'Лицензия'),
                        ('permit', 'Разрешение'),
                        ('notification', 'Уведомление'),
                        ('other', 'Иное'),
                    ],
                    max_length=20,
                )),
                ('doc_number', models.CharField(max_length=100)),
                ('issued_by', models.CharField(help_text='Орган, выдавший документ', max_length=255)),
                ('issued_date', models.DateField()),
                ('expires_at', models.DateField(
                    blank=True,
                    help_text='Оставьте пустым если документ бессрочный',
                    null=True,
                )),
                ('file', models.FileField(help_text='PDF, JPG или PNG', upload_to='permits/%Y/%m/')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'На проверке'),
                        ('approved', 'Подтверждён'),
                        ('rejected', 'Отклонён'),
                        ('expired', 'Истёк'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('rejection_reason', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(
                    limit_choices_to={'is_regulated': True},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='permit_documents',
                    to='platforms.category',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_permits',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='permit_documents',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Разрешительный документ',
                'verbose_name_plural': 'Разрешительные документы',
                'ordering': ['-created_at'],
            },
        ),
    ]
