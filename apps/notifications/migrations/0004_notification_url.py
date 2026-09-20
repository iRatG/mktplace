from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_notification_type_registration'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='url',
            field=models.CharField(blank=True, default='', help_text='Относительный адрес страницы, где нужно действие по этому уведомлению', max_length=255),
        ),
    ]
