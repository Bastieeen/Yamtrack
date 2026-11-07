"""Classer les séries « en cours » : terminées, abandonnées, ou à laisser en cours.

À lancer sur le serveur, dans le conteneur Yamtrack :

    curl -sL <url de ce fichier> | docker exec -i yamtrack python manage.py shell

Par défaut, rien n'est modifié : le script affiche seulement le classement.
Pour appliquer les changements, relancer avec  -e APPLIQUER=1  après « exec -i ».

Règles (demandées par Bastien) :
  - dernier épisode diffusé de la série vu, et série terminée ou annulée -> Terminée
  - dernier épisode vu qui n'est pas le dernier de la série, et aucun épisode
    vu depuis la date limite (1er juin 2026)                              -> Abandonnée
  - dernier épisode diffusé vu mais la série continue                    -> reste en cours
  - activité depuis la date limite                                       -> reste en cours
  - informations TMDB introuvables                                       -> à vérifier, inchangée
"""

import datetime
import os

from django.contrib.auth import get_user_model
from django.utils import timezone
from simple_history.utils import bulk_update_with_history

from app.models import TV, Episode, MediaTypes, Season, Status
from app.providers import services

DATE_LIMITE = datetime.date(2026, 6, 1)
STATUTS_FINIS = {"Ended", "Canceled"}

TERMINEE = "terminee"
ABANDONNEE = "abandonnee"
A_JOUR = "a_jour"
ACTIVE = "active"
A_VERIFIER = "a_verifier"

LIBELLES = {
    TERMINEE: "Passent en « Terminée » (dernier épisode de la série vu, série finie)",
    ABANDONNEE: "Passent en « Abandonnée » (pas au bout, rien vu depuis le 1er juin 2026)",
    A_JOUR: "Restent en cours : à jour, mais la série continue",
    ACTIVE: "Restent en cours : épisode vu depuis le 1er juin 2026",
    A_VERIFIER: "Inchangées : informations TMDB introuvables, à vérifier",
}


def dernier_episode_diffuse(tv, aujourd_hui):
    """Return (season_number, episode_number, series_status) of the last aired episode."""
    meta = services.get_media_metadata(
        MediaTypes.TV.value,
        tv.item.media_id,
        tv.item.source,
    )
    statut = (meta.get("details") or {}).get("status") or ""
    saison = meta.get("last_episode_season")
    if not saison:
        numeros = [
            s.get("season_number")
            for s in (meta.get("related") or {}).get("seasons") or []
            if s.get("season_number") and (s.get("max_progress") or 0) > 0
        ]
        saison = max(numeros) if numeros else None
    if not saison:
        return None

    meta_saison = services.get_media_metadata(
        MediaTypes.SEASON.value,
        tv.item.media_id,
        tv.item.source,
        [saison],
    )
    diffuses = []
    for episode in meta_saison.get("episodes") or []:
        air = episode.get("air_date")
        if air and datetime.date.fromisoformat(air) <= aujourd_hui:
            diffuses.append(episode["episode_number"])
    if not diffuses:
        return None
    return saison, max(diffuses), statut


def classer(tv, aujourd_hui):
    """Return (category, detail) for one in-progress show."""
    vus = set()
    derniere_activite = None
    # One short query per show (a single prefetch over every show is too large for SQLite).
    episodes = Episode.objects.filter(related_season__related_tv=tv).select_related("item")
    for episode in episodes:
        if episode.item.season_number:
            vus.add((episode.item.season_number, episode.item.episode_number))
        if episode.end_date:
            jour = timezone.localtime(episode.end_date).date()
            if derniere_activite is None or jour > derniere_activite:
                derniere_activite = jour

    try:
        final = dernier_episode_diffuse(tv, aujourd_hui)
    except Exception as exc:  # noqa: BLE001 - report and keep going
        return A_VERIFIER, f"erreur TMDB : {exc.__class__.__name__}"
    if final is None:
        return A_VERIFIER, "aucun épisode diffusé trouvé"

    saison, numero, statut = final
    vu_jusqu_au_bout = (saison, numero) in vus
    detail = (
        f"dernier diffusé S{saison:02d}E{numero:02d}, série « {statut or '?'} », "
        f"dernière activité {derniere_activite or 'aucune'}"
    )
    if vu_jusqu_au_bout:
        return (TERMINEE if statut in STATUTS_FINIS else A_JOUR), detail
    if derniere_activite is None or derniere_activite < DATE_LIMITE:
        return ABANDONNEE, detail
    return ACTIVE, detail


def appliquer(tv, nouveau_statut, user):
    """Set the show and its in-progress seasons to the new status, with history."""
    tv.status = nouveau_statut
    bulk_update_with_history([tv], TV, fields=["status"], default_user=user)
    saisons = list(tv.seasons.filter(status=Status.IN_PROGRESS.value))
    for saison in saisons:
        saison.status = nouveau_statut
    if saisons:
        bulk_update_with_history(saisons, Season, fields=["status"], default_user=user)


def main():
    appliquer_changements = os.environ.get("APPLIQUER") == "1"
    aujourd_hui = timezone.localdate()
    resultats = {cle: [] for cle in LIBELLES}

    for user in get_user_model().objects.all():
        series = list(
            TV.objects.filter(user=user, status=Status.IN_PROGRESS.value)
            .select_related("item")
            .order_by("item__title"),
        )
        total = len(series)
        print(f"Utilisateur {user.username} : {total} séries en cours, analyse…")
        for i, tv in enumerate(series, start=1):
            categorie, detail = classer(tv, aujourd_hui)
            resultats[categorie].append((user, tv, detail))
            if i % 25 == 0:
                print(f"  {i}/{total}")

    print()
    for cle, libelle in LIBELLES.items():
        lignes = resultats[cle]
        print(f"=== {libelle} : {len(lignes)}")
        for _user, tv, detail in lignes:
            print(f"  - {tv.item.title} ({detail})")
        print()

    if not appliquer_changements:
        print("Aucune modification faite. Pour appliquer : relancer avec -e APPLIQUER=1")
        return

    for cle, statut in ((TERMINEE, Status.COMPLETED.value), (ABANDONNEE, Status.DROPPED.value)):
        for user, tv, _detail in resultats[cle]:
            appliquer(tv, statut, user)
    print(
        f"Appliqué : {len(resultats[TERMINEE])} terminées, "
        f"{len(resultats[ABANDONNEE])} abandonnées.",
    )


if os.environ.get("CLASSER_SERIES_IMPORT") != "1":
    main()
