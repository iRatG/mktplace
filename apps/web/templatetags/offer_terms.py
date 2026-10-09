from django import template

register = template.Library()


@register.filter
def terms_rows(terms):
    """Снимок условий оферты → [(подпись, значение)] для показа исполнителю и сторонам сделки."""
    from apps.web.campaign_proposals import describe_terms

    return describe_terms(terms)
