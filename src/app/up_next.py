"""Helpers for the "Up next" watch list and the rolling releases page."""

import datetime
import logging

from django.utils import timezone

from app.models import BasicMedia, MediaTypes, Season, Status
from app.providers import services
from events.models import Event

logger = logging.getLogger(__name__)

RELEASES_HORIZON_DAYS = 30


def _parse_air_date(value):
    """Return a date from a TMDB air_date string, or None when unknown."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _season_episodes(media_id, source, season_number):
    """Return the raw episode list of a season from cached provider metadata."""
    try:
        metadata = services.get_media_metadata(
            MediaTypes.SEASON.value,
            media_id,
            source,
            [season_number],
        )
    except Exception:  # noqa: BLE001 - a provider error must not break the page
        logger.warning(
            "Could not load episodes for %s season %s", media_id, season_number,
            exc_info=True,
        )
        return []
    return sorted(metadata.get("episodes") or [], key=lambda e: e["episode_number"])


def _next_season_number(media_id, source, current_season_number):
    """Return the number of the season after the current one, if the show has one."""
    try:
        metadata = services.get_media_metadata(MediaTypes.TV.value, media_id, source)
    except Exception:  # noqa: BLE001
        logger.warning("Could not load seasons for %s", media_id, exc_info=True)
        return None
    numbers = [
        s.get("season_number")
        for s in (metadata.get("related") or {}).get("seasons") or []
        if s.get("season_number") and s["season_number"] > current_season_number
    ]
    return min(numbers) if numbers else None


def build_entry(season, today=None):
    """Describe the next episode to watch for one in-progress season.

    When every episode of the season has been watched (common after an import,
    where finished seasons stay "in progress"), point to the next season instead.
    """
    today = today or timezone.localdate()
    media_id, source = season.item.media_id, season.item.source
    target_season_number = season.item.season_number
    episodes = _season_episodes(media_id, source, target_season_number)
    watched = season.progress

    upcoming = [e for e in episodes if e["episode_number"] > watched]
    if episodes and not upcoming and target_season_number:
        next_number = _next_season_number(media_id, source, target_season_number)
        if next_number is not None:
            target_season_number = next_number
            upcoming = _season_episodes(media_id, source, next_number)
    next_episode = upcoming[0] if upcoming else None
    next_air_date = _parse_air_date(next_episode["air_date"]) if next_episode else None
    remaining = sum(
        1
        for e in upcoming
        if (air := _parse_air_date(e["air_date"])) is not None and air <= today
    )
    is_available = next_air_date is not None and next_air_date <= today

    last_end_date = max(
        (ep.end_date for ep in season.episodes.all() if ep.end_date),
        default=None,
    )

    return {
        "media": season,
        "item": season.item,
        "season_number": target_season_number,
        "is_next_season": target_season_number != season.item.season_number,
        "next_episode_number": next_episode["episode_number"] if next_episode else None,
        "next_episode_title": (next_episode.get("name") or "") if next_episode else "",
        "next_air_date": next_air_date,
        "remaining": remaining,
        "is_available": is_available,
        "last_end_date": last_end_date,
    }


def get_up_next(user):
    """Return in-progress seasons split into "to catch up" and "waiting"."""
    seasons = BasicMedia.objects.get_media_list(
        user=user,
        media_type=MediaTypes.SEASON.value,
        status_filter=Status.IN_PROGRESS.value,
        sort_filter=None,
    )
    today = timezone.localdate()
    entries = [build_entry(season, today) for season in seasons]

    catch_up = sorted(
        (e for e in entries if e["is_available"]),
        key=lambda e: e["last_end_date"] or timezone.make_aware(datetime.datetime.min),
        reverse=True,
    )
    waiting = sorted(
        (e for e in entries if not e["is_available"]),
        key=lambda e: e["next_air_date"] or datetime.date.max,
    )
    return {"catch_up": catch_up, "waiting": waiting}


def get_owned_season(user, season_id):
    """Return a season of the user, with episodes prefetched."""
    return (
        Season.objects.select_related("item", "related_tv", "related_tv__item")
        .prefetch_related("episodes__item")
        .get(id=season_id, user=user)
    )


def get_release_groups(user, today=None, horizon_days=RELEASES_HORIZON_DAYS):
    """Return upcoming releases grouped by today / tomorrow / this week / later."""
    today = today or timezone.localdate()
    last_day = today + datetime.timedelta(days=horizon_days)
    releases = Event.objects.get_user_events(user, today, last_day)

    tomorrow = today + datetime.timedelta(days=1)
    # Rolling week: the next 7 days, whatever the current weekday.
    week_end = today + datetime.timedelta(days=6)

    groups = {"today": [], "tomorrow": [], "this_week": [], "later": []}
    for release in releases:
        if release.is_max_datetime:
            continue
        day = timezone.localtime(release.datetime).date()
        if day < today:
            continue
        release.local_date = day
        release.is_premiere = release.content_number == 1
        if day == today:
            groups["today"].append(release)
        elif day == tomorrow:
            groups["tomorrow"].append(release)
        elif day <= week_end:
            groups["this_week"].append(release)
        else:
            groups["later"].append(release)
    return groups
