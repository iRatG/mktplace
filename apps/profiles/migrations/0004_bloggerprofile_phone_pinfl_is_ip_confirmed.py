from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0003_bloggerprofile_is_complete'),
    ]

    operations = [
        migrations.AddField(
            model_name='bloggerprofile',
            name='phone',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='bloggerprofile',
            name='pinfl',
            field=models.CharField(blank=True, max_length=14, verbose_name='ПИНФЛ'),
        ),
        migrations.AddField(
            model_name='bloggerprofile',
            name='is_ip_confirmed',
            field=models.BooleanField(default=False),
        ),
    ]
