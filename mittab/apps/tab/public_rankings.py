from enum import IntEnum

from mittab.apps.tab.models import PublicDisplaySetting, Round
from mittab.libs.cacheing import cache_logic

RANKING_SLUGS = ("team", "varsity", "novice")
STANDING_SLUGS = ("speaker_results", "team_results")
DISPLAY_FLAGS_CACHE_KEY = "public_display_flags"

class PublicRankingMode(IntEnum):
    NONE = 0
    TEAM = 1
    LAST_BALLOTS = 2
    ALL_BALLOTS = 3


def _setting(slug, *, label, display_type, defaults=None):
    return PublicDisplaySetting.get_or_create_setting(
        slug=slug,
        label=label,
        display_type=display_type,
        defaults=defaults or {},
    )[0]


def get_ranking_settings(slug):
    label, defaults = _ranking_metadata(slug)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.RANKING,
        defaults=defaults,
    )
    return {
        "slug": slug,
        "label": label,
        "public": setting.is_enabled,
        "include_speaks": setting.include_speaks,
        "max_visible": setting.max_visible,
        "up_to_round": setting.round_number,
    }


def set_ranking_settings(slug, public, include_speaks, max_visible, up_to_round=None):
    label, defaults = _ranking_metadata(slug)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.RANKING,
        defaults=defaults,
    )
    _update_setting(
        setting,
        is_enabled=bool(public),
        include_speaks=bool(include_speaks),
        max_visible=max(1, int(max_visible)),
        round_number=up_to_round,
    )
    invalidate_public_display_flags_cache()


def get_all_ranking_settings():
    return [get_ranking_settings(slug) for slug in RANKING_SLUGS]


def get_paired_round_numbers():
    """Return sorted round numbers that have been paired (have Round objects)."""
    return list(
        Round.objects.values_list("round_number", flat=True)
        .distinct()
        .order_by("round_number")
    )


def get_standings_publication_setting(slug):
    label = _standing_label(slug)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.STANDING,
    )
    return {
        "slug": slug,
        "label": label,
        "published": setting.is_enabled,
    }


def set_standings_publication_setting(slug, published):
    label = _standing_label(slug)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.STANDING,
    )
    _update_setting(setting, is_enabled=bool(published))


def get_all_standings_publication_settings():
    return [get_standings_publication_setting(slug) for slug in STANDING_SLUGS]


def get_ballot_round_settings(round_number):
    slug, label = _ballot_metadata(round_number)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.BALLOT,
        defaults={"round_number": round_number},
    )
    return {
        "round_number": round_number,
        "visible": setting.is_enabled,
        "include_speaks": setting.include_speaks,
        "include_ranks": setting.include_ranks,
    }


def set_ballot_round_settings(round_number, visible, include_speaks, include_ranks):
    slug, label = _ballot_metadata(round_number)
    setting = _setting(
        slug,
        label=label,
        display_type=PublicDisplaySetting.BALLOT,
        defaults={"round_number": round_number},
    )
    _update_setting(
        setting,
        is_enabled=bool(visible),
        include_speaks=bool(include_speaks),
        include_ranks=bool(include_ranks),
    )
    invalidate_public_display_flags_cache()


def get_all_ballot_round_settings(tot_rounds):
    return [
        get_ballot_round_settings(round_number)
        for round_number in range(1, tot_rounds + 1)
    ]


def _load_public_display_flags():
    rankings = {slug: get_ranking_settings(slug)["public"] for slug in RANKING_SLUGS}
    ballots_public = PublicDisplaySetting.objects.filter(
        display_type=PublicDisplaySetting.BALLOT,
        is_enabled=True,
    ).exists()

    return {
        "team_results": bool(rankings.get("team")),
        "speaker_results": bool(rankings.get("varsity") or rankings.get("novice")),
        "ballots": bool(ballots_public),
    }


def get_public_display_flags():
    cached = cache_logic.cache_fxn_key(
        _load_public_display_flags,
        DISPLAY_FLAGS_CACHE_KEY,
        cache_logic.DEFAULT,
    )
    return dict(cached)


def invalidate_public_display_flags_cache():
    cache_logic.invalidate_cache(DISPLAY_FLAGS_CACHE_KEY)


def _ranking_metadata(slug):
    if slug == "team":
        return "Team", {"max_visible": 1000}
    if slug == "varsity":
        return "Varsity Speakers", {"max_visible": 10}
    if slug == "novice":
        return "Novice Speakers", {"max_visible": 10}
    raise ValueError(f"Unknown ranking slug '{slug}'")


def _standing_label(slug):
    if slug == "speaker_results":
        return "Speaker Results"
    if slug == "team_results":
        return "Team Results"
    raise ValueError(f"Unknown standing slug '{slug}'")


def _ballot_metadata(round_number):
    if round_number < 1:
        raise ValueError("Round number must be positive")
    slug = f"ballot_round_{round_number}"
    label = f"Round {round_number} Ballots"
    return slug, label


def _update_setting(setting, **fields):
    for field, value in fields.items():
        setattr(setting, field, value)
    setting.save(update_fields=list(fields.keys()))
