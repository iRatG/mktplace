from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deals', '0004_cpa_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='deal',
            name='last_distributed_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='Дата последнего распространения рекламы. От этой даты отсчитывается 3-летний срок хранения материалов.',
            ),
        ),
        migrations.AddField(
            model_name='deal',
            name='is_frozen',
            field=models.BooleanField(
                default=False,
                help_text='Материалы заморожены (активный или завершённый спор) — удаление запрещено до истечения 3 лет.',
            ),
        ),
    ]
