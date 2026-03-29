def cpa_click_track(request, slug):
    """Публичный endpoint: /t/<slug>/

    Логирует клик, создаёт конверсию (для click-type сразу начисляет).
    Редиректит на cpa_tracking_url кампании или на '/' если не задан.
    Авторизация не требуется — публичная ссылка для конечных пользователей.
    Rate limit: 30 кликов в час с одного IP (через Django cache / Redis).
    """
    from django.core.cache import cache
    from django.shortcuts import redirect
    from apps.deals.models import ClickLog, Conversion, TrackingLink
    from apps.billing.services import BillingService

    try:
        tl = TrackingLink.objects.select_related(
            "deal__campaign"
        ).get(slug=slug, is_active=True)
    except TrackingLink.DoesNotExist:
        from django.http import Http404
        raise Http404

    # Log the click
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or None
    )

    # Rate limit: max 30 clicks per hour per IP per slug
    rate_key = f"cpa_click:{ip}:{slug}"
    click_count = cache.get(rate_key, 0)
    if click_count >= 30:
        # Silently redirect — don't break UX, just skip logging
        target_url = tl.deal.campaign.cpa_tracking_url or "/"
        return redirect(target_url)
    cache.set(rate_key, click_count + 1, timeout=3600)

    ua = request.META.get("HTTP_USER_AGENT", "")[:1000]
    click = ClickLog.objects.create(tracking_link=tl, ip=ip, user_agent=ua)

    campaign = tl.deal.campaign
    cpa_type = campaign.cpa_type or ""
    cpa_rate = campaign.cpa_rate

    if cpa_type == campaign.CPAType.CLICK and cpa_rate:
        # Immediate conversion + billing
        conversion = Conversion.objects.create(
            tracking_link=tl,
            click_log=click,
            conversion_type=Conversion.ConversionType.CLICK,
            amount=cpa_rate,
        )
        try:
            BillingService.credit_cpa_conversion(conversion)
        except ValueError:
            pass  # insufficient funds — conversion stays uncredited

    # Redirect to target URL, appending click_id for postback attribution
    target_url = campaign.cpa_tracking_url or "/"
    if "?" in target_url:
        target_url += f"&click_id={click.click_id}"
    else:
        target_url += f"?click_id={click.click_id}"

    return redirect(target_url)


def cpa_postback(request):
    """Публичный postback endpoint: /pb/?click_id=UUID&goal=lead

    Принимает GET/POST.
    Обязательный параметр: click_id (UUID)
    Опциональный: goal (lead/sale/install), по умолчанию lead.
    Создаёт Conversion и начисляет через BillingService.
    Идемпотентен: повторный постбек с тем же click_id игнорируется.
    Rate limit: 100 постбэков в час с одного IP.
    """
    from django.core.cache import cache
    from django.http import JsonResponse
    from apps.deals.models import ClickLog, Conversion
    from apps.billing.services import BillingService

    # Rate limit: max 100 postbacks per hour per IP
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    )
    pb_rate_key = f"cpa_postback:{ip}"
    pb_count = cache.get(pb_rate_key, 0)
    if pb_count >= 100:
        return JsonResponse({"status": "error", "detail": "rate limit exceeded"}, status=429)
    cache.set(pb_rate_key, pb_count + 1, timeout=3600)

    params = request.GET if request.method == "GET" else request.POST
    click_id_raw = params.get("click_id", "").strip()
    goal = params.get("goal", "lead").strip().lower()

    if not click_id_raw:
        return JsonResponse({"status": "error", "detail": "click_id required"}, status=400)

    try:
        import uuid
        click_id = uuid.UUID(click_id_raw)
    except ValueError:
        return JsonResponse({"status": "error", "detail": "invalid click_id"}, status=400)

    try:
        click = ClickLog.objects.select_related(
            "tracking_link__deal__campaign"
        ).get(click_id=click_id)
    except ClickLog.DoesNotExist:
        return JsonResponse({"status": "error", "detail": "click not found"}, status=404)

    # Map goal → ConversionType
    goal_map = {
        "lead": Conversion.ConversionType.LEAD,
        "sale": Conversion.ConversionType.SALE,
        "install": Conversion.ConversionType.INSTALL,
    }
    conv_type = goal_map.get(goal, Conversion.ConversionType.LEAD)

    # Idempotency: one credited conversion per click per goal
    already = Conversion.objects.filter(
        click_log=click, conversion_type=conv_type, credited=True
    ).exists()
    if already:
        return JsonResponse({"status": "ok", "detail": "already credited"})

    campaign = click.tracking_link.deal.campaign
    cpa_rate = campaign.cpa_rate
    if not cpa_rate:
        return JsonResponse({"status": "error", "detail": "no cpa_rate on campaign"}, status=400)

    import json as _json
    postback_raw = _json.dumps(dict(params))

    conversion = Conversion.objects.create(
        tracking_link=click.tracking_link,
        click_log=click,
        conversion_type=conv_type,
        amount=cpa_rate,
        postback_raw=postback_raw,
    )
    try:
        BillingService.credit_cpa_conversion(conversion)
        return JsonResponse({"status": "ok", "conversion_id": conversion.pk})
    except ValueError as e:
        return JsonResponse({"status": "error", "detail": str(e)}, status=402)
