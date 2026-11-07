import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import up_next
from app.models import TV, Episode, Item, MediaTypes, Season, Sources, Status
from events.models import Event

TODAY = timezone.localdate()


def _episodes():
    def day(offset):
        return (TODAY + datetime.timedelta(days=offset)).isoformat()

    return [
        {"episode_number": 1, "name": "Pilot", "air_date": day(-30), "still_path": None},
        {"episode_number": 2, "name": "Second", "air_date": day(-20), "still_path": None},
        {"episode_number": 3, "name": "Third", "air_date": day(-10), "still_path": None},
        {"episode_number": 4, "name": "Future", "air_date": day(10), "still_path": None},
    ]


def fake_metadata(media_type, media_id, source, season_numbers=None, *args, **kwargs):  # noqa: ARG001
    """Stand-in for provider metadata so tests never reach the network."""
    season = {
        "title": "Show",
        "season_title": "Season 1",
        "image": "http://example.com/s1.jpg",
        "season_number": 1,
        "max_progress": 4,
        "episodes": _episodes(),
    }
    if media_type == "tv_with_seasons":
        return {"title": "Show", "image": "http://example.com/tv.jpg", "season/1": season}
    return season


class UpNextTests(TestCase):
    """Watch list, swipe actions and undo."""

    def setUp(self):
        for target, kwargs in (
            ("app.providers.services.get_media_metadata", {"side_effect": fake_metadata}),
            ("app.models.Item.fetch_releases", {}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        tv_item = Item.objects.create(
            media_id="42",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Show",
            image="http://example.com/tv.jpg",
        )
        # save_base skips the show's save hooks, which would look up other seasons.
        self.tv = TV(item=tv_item, user=self.user, status=Status.IN_PROGRESS.value)
        TV.save_base(self.tv)
        season_item = Item.objects.create(
            media_id="42",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Show",
            image="http://example.com/s1.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )
        self._watch_without_signals(1)

    def _watch_without_signals(self, number):
        item = Item.objects.create(
            media_id="42",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Show",
            image="http://example.com/e.jpg",
            season_number=1,
            episode_number=number,
        )
        Episode.save_base(
            Episode(
                item=item,
                related_season=self.season,
                end_date=timezone.now() - datetime.timedelta(days=1),
            ),
        )

    def test_page_lists_next_episode_and_remaining(self, *_mocks):
        response = self.client.get(reverse("up_next"))
        self.assertEqual(response.status_code, 200)
        entry = response.context["catch_up"][0]
        self.assertEqual(entry["next_episode_number"], 2)
        self.assertEqual(entry["next_episode_title"], "Second")
        self.assertEqual(entry["remaining"], 2)
        self.assertContains(response, "S01E02 · Second")
        self.assertContains(response, "2 épisodes restants")

    def test_caught_up_season_is_waiting(self, *_mocks):
        self._watch_without_signals(2)
        self._watch_without_signals(3)
        data = up_next.get_up_next(self.user)
        self.assertEqual(data["catch_up"], [])
        self.assertEqual(data["waiting"][0]["next_episode_number"], 4)

    def test_watch_marks_next_episode_and_offers_undo(self, *_mocks):
        response = self.client.post(reverse("up_next_watch", args=[self.season.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Episode.objects.filter(
                related_season=self.season,
                item__episode_number=2,
            ).exists(),
        )
        self.assertContains(response, "S01E02 marqué comme vu")
        self.assertContains(response, "S01E03 · Third")

    def test_undo_watch_removes_episode(self, *_mocks):
        self.client.post(reverse("up_next_watch", args=[self.season.id]))
        response = self.client.post(
            reverse("up_next_undo", args=[self.season.id]),
            {"episode_number": "2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Episode.objects.filter(
                related_season=self.season,
                item__episode_number=2,
            ).exists(),
        )
        self.assertContains(response, "S01E02 · Second")

    def test_drop_and_undo(self, *_mocks):
        response = self.client.post(
            reverse("up_next_status", args=[self.season.id]),
            {"status": Status.DROPPED.value},
        )
        self.assertContains(response, "abandonnée")
        self.tv.refresh_from_db()
        self.season.refresh_from_db()
        self.assertEqual(self.tv.status, Status.DROPPED.value)
        self.assertEqual(self.season.status, Status.DROPPED.value)
        self.assertEqual(up_next.get_up_next(self.user)["catch_up"], [])

        self.client.post(
            reverse("up_next_undo", args=[self.season.id]),
            {"previous_status": Status.IN_PROGRESS.value},
        )
        self.tv.refresh_from_db()
        self.season.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)

    def test_pause_hides_show(self, *_mocks):
        self.client.post(
            reverse("up_next_status", args=[self.season.id]),
            {"status": Status.PAUSED.value},
        )
        self.tv.refresh_from_db()
        self.season.refresh_from_db()
        self.assertEqual(self.tv.status, Status.PAUSED.value)
        self.assertEqual(self.season.status, Status.PAUSED.value)
        data = up_next.get_up_next(self.user)
        self.assertEqual(data["catch_up"] + data["waiting"], [])

    def test_invalid_status_rejected(self, *_mocks):
        response = self.client.post(
            reverse("up_next_status", args=[self.season.id]),
            {"status": Status.COMPLETED.value},
        )
        self.assertEqual(response.status_code, 400)

    def test_other_users_season_is_404(self, *_mocks):
        other = get_user_model().objects.create_user(username="other", password="x")
        self.client.logout()
        self.client.login(username="other", password="x")
        response = self.client.post(reverse("up_next_watch", args=[self.season.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(other.is_authenticated)

    def test_releases_grouping(self, *_mocks):
        now = timezone.localtime()
        noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

        def at(days):
            return noon + datetime.timedelta(days=days)

        Event.objects.create(item=self.season.item, content_number=4, datetime=at(0))
        Event.objects.create(item=self.season.item, content_number=5, datetime=at(1))
        Event.objects.create(item=self.season.item, content_number=8, datetime=at(5))
        Event.objects.create(item=self.season.item, content_number=6, datetime=at(20))
        Event.objects.create(item=self.season.item, content_number=7, datetime=at(45))

        groups = up_next.get_release_groups(self.user)
        self.assertEqual([r.content_number for r in groups["today"]], [4])
        self.assertEqual([r.content_number for r in groups["tomorrow"]], [5])
        self.assertEqual([r.content_number for r in groups["this_week"]], [8])
        self.assertIn(6, [r.content_number for r in groups["later"]])
        all_numbers = [r.content_number for g in groups.values() for r in g]
        self.assertNotIn(7, all_numbers)

        response = self.client.get(reverse("releases"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S01E04")
        self.assertContains(response, "Aujourd'hui")
        self.assertContains(response, "Demain")

    def test_releases_hide_dropped_shows(self, *_mocks):
        Event.objects.create(
            item=self.season.item,
            content_number=4,
            datetime=timezone.now() + datetime.timedelta(days=2),
        )
        self.tv.status = Status.DROPPED.value
        self.tv.save()
        groups = up_next.get_release_groups(self.user)
        self.assertEqual(sum(len(g) for g in groups.values()), 0)

    def test_premiere_flag(self, *_mocks):
        Event.objects.create(
            item=self.season.item,
            content_number=1,
            datetime=timezone.now() + datetime.timedelta(days=3),
        )
        groups = up_next.get_release_groups(self.user)
        releases = [r for g in groups.values() for r in g]
        self.assertTrue(releases[0].is_premiere)


def _multi_season_metadata(media_type, media_id, source, season_numbers=None, *a, **k):  # noqa: ARG001
    """Show with a finished season 1 (3 episodes) and a season 2 still airing."""

    def day(offset):
        return (TODAY + datetime.timedelta(days=offset)).isoformat()

    seasons = {
        1: [{"episode_number": n, "name": f"S1 {n}", "air_date": day(-100 + n), "still_path": None}
            for n in (1, 2, 3)],
        2: [
            {"episode_number": 1, "name": "Retour", "air_date": day(-14), "still_path": None},
            {"episode_number": 2, "name": "Suite", "air_date": day(-7), "still_path": None},
            {"episode_number": 3, "name": "Plus tard", "air_date": day(7), "still_path": None},
        ],
    }

    def season(n):
        return {"title": "Show", "image": f"http://example.com/s{n}.jpg", "season_number": n,
                "max_progress": len(seasons[n]), "episodes": seasons[n]}

    if media_type == MediaTypes.TV.value:
        return {"title": "Show", "related": {"seasons": [{"season_number": 1}, {"season_number": 2}]}}
    if media_type == "tv_with_seasons":
        return {"title": "Show", **{f"season/{n}": season(n) for n in season_numbers}}
    return season(season_numbers[0])


class NextSeasonTests(TestCase):
    """A fully watched season left "in progress" points to the next season."""

    def setUp(self):
        for target, kwargs in (
            ("app.providers.services.get_media_metadata", {"side_effect": _multi_season_metadata}),
            ("app.models.Item.fetch_releases", {}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.user = get_user_model().objects.create_user(username="n", password="12345")
        self.client.login(username="n", password="12345")
        tv_item = Item.objects.create(media_id="77", source=Sources.TMDB.value,
                                      media_type=MediaTypes.TV.value, title="Show", image="http://x/t.jpg")
        self.tv = TV(item=tv_item, user=self.user, status=Status.IN_PROGRESS.value)
        TV.save_base(self.tv)
        s1_item = Item.objects.create(media_id="77", source=Sources.TMDB.value,
                                      media_type=MediaTypes.SEASON.value, title="Show",
                                      image="http://x/s1.jpg", season_number=1)
        self.season1 = Season(item=s1_item, user=self.user, related_tv=self.tv,
                              status=Status.IN_PROGRESS.value)
        Season.save_base(self.season1)
        for n in (1, 2, 3):
            ep_item = Item.objects.create(media_id="77", source=Sources.TMDB.value,
                                          media_type=MediaTypes.EPISODE.value, title="Show",
                                          image="http://x/e.jpg", season_number=1, episode_number=n)
            Episode.save_base(Episode(item=ep_item, related_season=self.season1,
                                      end_date=timezone.now() - datetime.timedelta(days=30 - n)))

    def test_entry_points_to_next_season(self):
        entry = up_next.build_entry(self.season1)
        self.assertTrue(entry["is_next_season"])
        self.assertEqual(entry["season_number"], 2)
        self.assertEqual(entry["next_episode_number"], 1)
        self.assertEqual(entry["next_episode_title"], "Retour")
        self.assertEqual(entry["remaining"], 2)
        self.assertTrue(entry["is_available"])

    def test_page_shows_next_season_in_catch_up(self):
        response = self.client.get(reverse("up_next"))
        self.assertContains(response, "S02E01 · Retour")
        self.assertContains(response, "Saison 2 · 2 épisodes restants")

    def test_watch_moves_to_next_season(self):
        response = self.client.post(reverse("up_next_watch", args=[self.season1.id]))
        self.assertEqual(response.status_code, 200)
        self.season1.refresh_from_db()
        self.assertEqual(self.season1.status, Status.COMPLETED.value)
        season2 = Season.objects.get(related_tv=self.tv, item__season_number=2)
        self.assertEqual(season2.status, Status.IN_PROGRESS.value)
        self.assertTrue(season2.episodes.filter(item__episode_number=1).exists())
        self.assertContains(response, "S02E01 marqué comme vu")
        self.assertContains(response, "S02E02 · Suite")
        self.assertContains(response, f'id="up-next-row-{season2.id}"')
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
