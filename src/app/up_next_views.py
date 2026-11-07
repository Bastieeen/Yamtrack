"""Views for the "Up next" watch list, swipe actions and the releases page."""

import logging

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from app import up_next
from app.models import Season, Status

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


@require_POST
def up_next_watch(request, season_id):
    """Mark the next episode of a season as watched."""
    season = _season_or_404(request, season_id)
    entry = up_next.build_entry(season)
    episode_number = entry["next_episode_number"]
    if episode_number is None:
        return HttpResponseBadRequest("No episode left to watch")

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
