from mittab.apps.tab.models import PublicDisplaySetting
from mittab.libs.cacheing import cache_logic

RANKING_CONFIG = {
    "team": {"label": "Team", "defaults": {"max_visible": 1000}},
    "varsity": {"label": "Varsity Speakers", "defaults": {"max_visible": 10}},
    "novice": {"label": "Novice Speakers", "defaults": {"max_visible": 10}},
}

STANDING_CONFIG = {
    "speaker_results": {"label": "Speaker Results"},
    "team_results": {"label": "Team Results"},
}

BALLOT_LABEL_TEMPLATE = "Round {round} Ballots"


def _setting(slug, *, label, display_type, defaults=None):
    return PublicDisplaySetting.get_or_create_setting(
        slug=slug,
        label=label,
        display_type=display_type,
        defaults=defaults or {},
    )[0]


def get_ranking_settings(slug):
    config = RANKING_CONFIG[slug]
    setting = _setting(
        slug,
        label=config["label"],
        display_type=PublicDisplaySetting.RANKING,
        defaults=config.get("defaults"),
    )
    return {
        "slug": slug,
        "label": config["label"],
        "public": setting.is_enabled,
        "include_speaks": setting.include_speaks,
        "max_visible": setting.max_visible,
    }


def set_ranking_settings(slug, public, include_speaks, max_visible):
    setting = _setting(
        slug,
        label=RANKING_CONFIG[slug]["label"],
        display_type=PublicDisplaySetting.RANKING,
    )
    setting.is_enabled = bool(public)
    setting.include_speaks = bool(include_speaks)
    setting.max_visible = max(1, int(max_visible))
    setting.save(update_fields=["is_enabled", "include_speaks", "max_visible"])
    invalidate_public_display_flags_cache()


def get_all_ranking_settings():
    return [get_ranking_settings(slug) for slug in RANKING_CONFIG]


def get_standings_publication_setting(slug):
    config = STANDING_CONFIG[slug]
    setting = _setting(
        slug,
        label=config["label"],
        display_type=PublicDisplaySetting.STANDING,
    )
    return {
        "slug": slug,
        "label": config["label"],
        "published": setting.is_enabled,
    }


def set_standings_publication_setting(slug, published):
    setting = _setting(
        slug,
        label=STANDING_CONFIG[slug]["label"],
        display_type=PublicDisplaySetting.STANDING,
    )
    setting.is_enabled = bool(published)
    setting.save(update_fields=["is_enabled"])


def get_all_standings_publication_settings():
    return [get_standings_publication_setting(slug) for slug in STANDING_CONFIG]


def get_ballot_round_settings(round_number):
    slug = f"ballot_round_{round_number}"
    setting = _setting(
        slug,
        label=BALLOT_LABEL_TEMPLATE.format(round=round_number),
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
    setting = _setting(
        f"ballot_round_{round_number}",
        label=BALLOT_LABEL_TEMPLATE.format(round=round_number),
        display_type=PublicDisplaySetting.BALLOT,
        defaults={"round_number": round_number},
    )
    setting.is_enabled = bool(visible)
    setting.include_speaks = bool(include_speaks)
    setting.include_ranks = bool(include_ranks)
    setting.save(update_fields=["is_enabled", "include_speaks", "include_ranks"])
    invalidate_public_display_flags_cache()


def get_all_ballot_round_settings(tot_rounds):
    return [get_ballot_round_settings(round_number) for round_number in range(1, tot_rounds + 1)]


DISPLAY_FLAGS_CACHE_KEY = "public_display_flags"


def _load_public_display_flags():
    team_public = get_ranking_settings("team")["public"]
    varsity_public = get_ranking_settings("varsity")["public"]
    novice_public = get_ranking_settings("novice")["public"]
    ballots_public = PublicDisplaySetting.objects.filter(
        display_type=PublicDisplaySetting.BALLOT,
        is_enabled=True,
    ).exists()

    return {
        "team_results": bool(team_public),
        "speaker_results": bool(varsity_public or novice_public),
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
