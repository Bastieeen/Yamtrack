from django.urls import path, register_converter

from app import converters, up_next_views, views

register_converter(converters.MediaTypeChecker, "media_type")
register_converter(converters.SourceChecker, "source")


urlpatterns = [
    path("", views.home, name="home"),
    path("up-next", up_next_views.up_next_page, name="up_next"),
    path("up-next/<int:season_id>/watch", up_next_views.up_next_watch, name="up_next_watch"),
    path("up-next/<int:season_id>/undo", up_next_views.up_next_undo, name="up_next_undo"),
    path("up-next/<int:season_id>/status", up_next_views.up_next_status, name="up_next_status"),
    path("releases", up_next_views.releases_page, name="releases"),
    path("<str:username>/<media_type:media_type>", views.media_list, name="medialist"),
    path("search", views.media_search, name="search"),
    path(
        "details/<source:source>/<media_type:media_type>/<str:media_id>/<str:title>",
        views.media_details,
        name="media_details",
    ),
    path(
        "details/<source:source>/tv/<str:media_id>/<str:title>/season/<int:season_number>",
        views.season_details,
        name="season_details",
    ),
    path(
        "update-score/<media_type:media_type>/<int:instance_id>",
        views.update_media_score,
        name="update_media_score",
    ),
    path(
        "details/sync/<source:source>/<media_type:media_type>/<str:media_id>",
        views.sync_metadata,
        name="sync_metadata",
    ),
    path(
        "details/sync/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.sync_metadata,
        name="sync_metadata",
    ),
    path(
        "track_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.track_modal,
        name="track_modal",
    ),
    path(
        "track_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.track_modal,
        name="track_modal",
    ),
    path(
        "progress_edit/<media_type:media_type>/<int:instance_id>",
        views.progress_edit,
        name="progress_edit",
    ),
    path("media_save", views.media_save, name="media_save"),
    path("media_delete", views.media_delete, name="media_delete"),
    path(
        "user_messages/mark_shown",
        views.mark_user_messages_shown,
        name="mark_user_messages_shown",
    ),
    path("episode_save", views.episode_save, name="episode_save"),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>/<int:episode_number>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "media/history/<str:media_type>/<int:history_id>/delete/",
        views.delete_history_record,
        name="delete_history_record",
    ),
    path("create", views.create_entry, name="create_entry"),
    path("search/parent_tv", views.search_parent_tv, name="search_parent_tv"),
    path(
        "search/parent_season",
        views.search_parent_season,
        name="search_parent_season",
    ),
    path("statistics", views.statistics, name="statistics"),
    path("journal", views.journal, name="journal"),
    path("serviceworker.js", views.service_worker, name="service_worker"),
]
