"""Стартовый список категорий, согласованный с бизнесом 04.10.2026.

Один список и для кампаний, и для площадок блогеров (общая модель Category).
get_or_create по имени: существующие «Lifestyle & ЗОЖ» (lifestyle) и «Технологии & IT»
(tech) не меняются и не дублируются, их связи сохраняются. is_regulated не трогаем —
регулируемость категорий решает staff в панели. Откат ничего не удаляет: к категориям
могли уже привязаться кампании и площадки.
"""
from django.db import migrations

BUSINESS_CATEGORIES = [
    ("Lifestyle & ЗОЖ", "lifestyle"),
    ("Технологии & IT", "tech"),
    ("Красота и уход", "beauty"),
    ("Мода и одежда", "fashion"),
    ("Еда и рестораны", "food"),
    ("Путешествия", "travel"),
    ("Спорт и фитнес", "sport"),
    ("Авто", "auto"),
    ("Финансы и бизнес", "finance"),
    ("Образование", "education"),
    ("Дети и семья", "kids-family"),
    ("Игры и киберспорт", "games"),
    ("Юмор и развлечения", "humor"),
    ("Музыка и кино", "music-movies"),
    ("Дом и недвижимость", "home-realty"),
    ("Электроника и онлайн-покупки", "electronics-shopping"),
    ("Другое", "other"),
]


def load_business_categories(apps, schema_editor):
    Category = apps.get_model("platforms", "Category")
    for name, slug in BUSINESS_CATEGORIES:
        if Category.objects.filter(name=name).exists():
            continue
        if Category.objects.filter(slug=slug).exists():
            # slug уже занят категорией с другим именем — не перезаписываем чужое.
            slug = f"{slug}-cat"
        Category.objects.create(name=name, slug=slug)


class Migration(migrations.Migration):

    dependencies = [
        ("platforms", "0003_category_regulated_permitdocument"),
    ]

    operations = [
        migrations.RunPython(load_business_categories, migrations.RunPython.noop),
    ]
