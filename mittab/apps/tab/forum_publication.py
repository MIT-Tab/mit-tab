"""Publish the tournament's results as one reply to its APDA Forum topic."""

import hashlib
import json
from urllib.parse import urlsplit

import requests
from django.conf import settings

from mittab.apps.tab.models import Judge, Outround


class ForumPublicationError(Exception):
    """A results post could not be confirmed."""


def publication_enabled():
    return bool(
        settings.APDA_FORUM_URL
        and settings.APDA_FORUM_PUBLICATION_ID
        and settings.APDA_FORUM_TOURNAMENT_TOKEN
    )


def finals_missing():
    finals = set(Outround.objects.filter(num_teams=2, victor__in=(
        Outround.GOV, Outround.OPP, Outround.GOV_VIA_FORFEIT, Outround.OPP_VIA_FORFEIT,
    )).values_list("type_of_round", flat=True))
    missing = []
    if Outround.VARSITY not in finals:
        missing.append("Enter the varsity finals decision.")
    if (
        Outround.NOVICE not in finals
        and Outround.objects.filter(type_of_round=Outround.NOVICE).exists()
    ):
        missing.append("Enter the novice finals decision.")
    return missing


def dino_roster():
    roster = list(Judge.objects.filter(is_dino=True).order_by("name", "pk").values_list("pk", "name"))
    confirmation = hashlib.sha256(json.dumps(roster).encode()).hexdigest()
    return [name for _, name in roster], confirmation


def results_body(context):
    lines = [
        "Thank you for a great tournament!",
        f"{context['team_count']} teams and {context['novice_count']} novices competed in at least three rounds.",
    ]
    if context["dinos"]:
        lines.append("Thanks to our dino judges: " + ", ".join(context["dinos"]) + ".")

    for division in ("Varsity", "Novice"):
        prefix = division.lower()
        champion = context[f"{prefix}_champ"]
        if champion:
            lines.append(f"\n{division} champions: {champion.display}")
        speakers = context["nov_debaters" if prefix == "novice" else "varsity_debaters"]
        if speakers:
            lines.append(f"\n{division} speaker awards")
            place = 1
            for index, (debater, _, _, _, tiebreaker) in enumerate(speakers, start=1):
                lines.append(f"{place}. {debater.name}")
                if tiebreaker != "Tie not broken":
                    place = index + 1
        if context[f"{prefix}_teams"]:
            lines.append(f"\n{division} breaking teams")
            lines.extend(f"{index}. {team.display}" for index, (team, *_) in
                         enumerate(context[f"{prefix}_teams"], start=1))
        for group in context[f"{prefix}_outs"]:
            lines.append(f"\n{group['label']}")
            lines.extend(group["results"])
    return "\n".join(lines)


def publish_results(body):
    """Return the forum URL. Repeat calls use the same publication identity."""
    endpoint = (
        f"{settings.APDA_FORUM_URL}/wp-json/apda-forum/v1/tournaments/"
        f"{settings.APDA_FORUM_PUBLICATION_ID}/results"
    )
    try:
        response = requests.post(
            endpoint,
            json={"body": body},
            headers={"X-APDA-Tournament-Token": settings.APDA_FORUM_TOURNAMENT_TOKEN},
            timeout=(3, 10),
            allow_redirects=False,
        )
        if response.status_code in (403, 409, 410):
            raise ForumPublicationError(
                "The forum topic is unavailable for posting. Check that it exists, is approved, "
                "and is open for replies."
            )
        if response.status_code not in (200, 201):
            raise ForumPublicationError("The forum could not confirm publication. Try again.")
        url = response.json().get("url") or ""
        target = urlsplit(url)
        forum = urlsplit(settings.APDA_FORUM_URL)
        if target.scheme == forum.scheme == "https" and target.netloc == forum.netloc:
            return url
    except (requests.RequestException, ValueError, AttributeError) as error:
        raise ForumPublicationError("The forum could not confirm publication. Try again.") from error
    raise ForumPublicationError("The forum could not confirm publication. Try again.")
