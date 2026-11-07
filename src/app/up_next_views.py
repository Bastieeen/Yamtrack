"""Views for the "Up next" watch list, swipe actions and the releases page."""

import logging

from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from simple_history.utils import bulk_update_with_history

from app import up_next
from app.models import Episode, Item, MediaTypes, Season, Sources, Status
from app.providers import services

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {Status.PAUSED.value, Status.DROPPED.value}


def _season_or_404(request, season_id):
    try:
        return up_next.get_owned_season(request.user, season_id)
    except Season.DoesNotExist as exc:
        msg = "Season not found"
        raise Http404(msg) from exc


def _render_row(request, season, toast=None):
    """Render one watch-list row after an action, or remove it when finished."""
    season = _season_or_404(request, season.id)
    context = {"toast": toast}
    if season.status == Status.IN_PROGRESS.value:
        context["entry"] = up_next.build_entry(season)
    else:
        context["finished"] = season
    return render(request, "app/components/up_next_row.html", context)


@require_GET
def up_next_page(request):
    """List the next episode to watch for every in-progress season."""
    context = up_next.get_up_next(request.user)
    return render(request, "app/up_next.html", context)


def _move_to_season(season, season_number):
    """Close a fully watched season and return the user's season `season_number`.

    Statuses are written directly (with history) so the show's own save hooks
    do not start another season on their own.
    """
    season.status = Status.COMPLETED.value
    bulk_update_with_history([season], Season, fields=["status"], default_user=season.user)

    existing = Season.objects.filter(
        user=season.user,
        related_tv=season.related_tv,
        item__season_number=season_number,
    ).first()
    if existing is not None:
        if existing.status != Status.IN_PROGRESS.value:
            existing.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [existing], Season, fields=["status"], default_user=season.user,
            )
        return existing

    metadata = services.get_media_metadata(
        MediaTypes.SEASON.value,
        season.item.media_id,
        season.item.source,
        [season_number],
    )
    item, _created = Item.objects.get_or_create(
        media_id=season.item.media_id,
        source=season.item.source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
        defaults={
            "title": season.item.title,
            "image": metadata.get("image") or season.item.image,
        },
    )
    return Season.objects.create(
        item=item,
        user=season.user,
        related_tv=season.related_tv,
        score=None,
        status=Status.IN_PROGRESS.value,
        notes="",
    )


@require_POST
def up_next_watch(request, season_id):
    """Mark the next episode as watched, moving on to the next season if needed."""
    season = _season_or_404(request, season_id)
    entry = up_next.build_entry(season)
    episode_number = entry["next_episode_number"]
    if episode_number is None:
        return HttpResponseBadRequest("No episode left to watch")

    if entry["is_next_season"]:
        season = _move_to_season(season, entry["season_number"])

    season.watch(episode_number, timezone.now().replace(second=0, microsecond=0))
    toast = {
        "kind": "watched",
        "season_id": season.id,
        "season_number": season.item.season_number,
        "episode_number": episode_number,
    }
    return _render_row(request, season, toast)


@require_POST
def up_next_undo(request, season_id):
    """Undo the last swipe: unwatch an episode or restore the previous status."""
    season = _season_or_404(request, season_id)

    episode_number = request.POST.get("episode_number")
    previous_status = request.POST.get("previous_status")

    if episode_number:
        season.unwatch(int(episode_number))
    elif previous_status == Status.IN_PROGRESS.value:
        # Restore the season first so the show does not start another season.
        season.status = Status.IN_PROGRESS.value
        season.save()
        tv = season.related_tv
        tv.refresh_from_db()
        if tv.status != Status.IN_PROGRESS.value:
            tv.status = Status.IN_PROGRESS.value
            tv.save()
    else:
        return HttpResponseBadRequest("Nothing to undo")

    return _render_row(request, season)


@require_POST
def up_next_status(request, season_id):
    """Pause or drop a whole show from its watch-list row."""
    status = request.POST.get("status")
    if status not in ALLOWED_STATUSES:
        return HttpResponseBadRequest("Invalid status")

    season = _season_or_404(request, season_id)
    tv = season.related_tv
    tv.status = status
    tv.save()

    # Dropping the show already drops its in-progress seasons; pausing does not.
    season.refresh_from_db()
    if season.status == Status.IN_PROGRESS.value:
        season.status = status
        season.save()

    toast = {"kind": status.lower(), "season_id": season.id}
    return _render_row(request, season, toast)


@require_GET
def releases_page(request):
    """Rolling list of upcoming releases for followed media."""
    context = {
        "groups": up_next.get_release_groups(request.user),
        "horizon_days": up_next.RELEASES_HORIZON_DAYS,
    }
    return render(request, "app/releases.html", context)


EPISODE_ACTIONS = {"watch", "unwatch", "watch_until"}


def _get_or_create_season(user, media_id, source, season_number):
    """Return the user's season, creating it like the regular episode form does."""
    season = (
        Season.objects.filter(
            user=user,
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
        )
        .select_related("item", "related_tv")
        .first()
    )
    if season is not None or source != Sources.TMDB.value:
        return season

    metadata = services.get_media_metadata(
        "tv_with_seasons", media_id, source, [season_number],
    )
    item, _created = Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
        defaults={
            "title": metadata["title"],
            "image": metadata[f"season/{season_number}"]["image"],
        },
    )
    return Season.objects.create(
        item=item,
        user=user,
        score=None,
        status=Status.IN_PROGRESS.value,
        notes="",
    )


@require_POST
def episode_toggle(request):
    """Season page: tick or untick one episode, or mark it and all earlier ones."""
    try:
        media_id = request.POST["media_id"]
        source = request.POST["source"]
        season_number = int(request.POST["season_number"])
        episode_number = int(request.POST["episode_number"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest("Invalid episode")
    action = request.POST.get("action")
    if action not in EPISODE_ACTIONS:
        return HttpResponseBadRequest("Invalid action")

    season = _get_or_create_season(request.user, media_id, source, season_number)
    if season is None:
        return HttpResponseBadRequest("Season not tracked")

    now = timezone.now().replace(second=0, microsecond=0)
    if action == "watch":
        season.watch(episode_number, now)
    elif action == "unwatch":
        season.unwatch(episode_number)
    else:
        watched = set(season.episodes.values_list("item__episode_number", flat=True))
        episodes = up_next._season_episodes(media_id, source, season_number)  # noqa: SLF001
        for number in sorted(e["episode_number"] for e in episodes):
            if number < episode_number and number not in watched:
                season.watch(number, None)  # no date: keeps viewing statistics honest
        if episode_number not in watched:
            season.watch(episode_number, now)
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response

    is_watched = Episode.objects.filter(
        related_season=season,
        item__episode_number=episode_number,
    ).exists()
    return render(
        request,
        "app/components/episode_quick_toggle.html",
        {
            "media_id": media_id,
            "source": source,
            "season_number": season_number,
            "episode_number": episode_number,
            "is_watched": is_watched,
        },
    )
