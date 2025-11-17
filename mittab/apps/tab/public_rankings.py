from dataclasses import dataclass
from typing import Dict, List

from mittab.apps.tab.models import TabSettings


@dataclass(frozen=True)
class RankingTypeConfig:
    slug: str
    label: str
    public_key: str
    include_speaks_key: str
    max_visible_key: str
    default_max_visible: int


@dataclass(frozen=True)
class StandingsPublicationConfig:
    slug: str
    label: str
    setting_key: str


RANKING_TYPES: Dict[str, RankingTypeConfig] = {
    "team": RankingTypeConfig(
        slug="team",
        label="Team",
        public_key="public_rankings_team_public",
        include_speaks_key="public_rankings_team_include_speaks",
        max_visible_key="public_rankings_team_max_visible",
        default_max_visible=1000,
    ),
    "varsity": RankingTypeConfig(
        slug="varsity",
        label="Varsity Speakers",
        public_key="public_rankings_varsity_public",
        include_speaks_key="public_rankings_varsity_include_speaks",
        max_visible_key="public_rankings_varsity_max_visible",
        default_max_visible=10,
    ),
    "novice": RankingTypeConfig(
        slug="novice",
        label="Novice Speakers",
        public_key="public_rankings_novice_public",
        include_speaks_key="public_rankings_novice_include_speaks",
        max_visible_key="public_rankings_novice_max_visible",
        default_max_visible=10,
    ),
}

STANDINGS_PUBLICATION_TYPES: Dict[str, StandingsPublicationConfig] = {
    "speaker_results": StandingsPublicationConfig(
        slug="speaker_results",
        label="Speaker Results",
        setting_key="standings_speaker_results_published",
    ),
    "team_results": StandingsPublicationConfig(
        slug="team_results",
        label="Team Results",
        setting_key="standings_team_results_published",
    ),
}


def _get_bool(key: str, default: int = 0) -> bool:
    return bool(int(TabSettings.get(key, default) or 0))


def _get_int(key: str, default: int) -> int:
    raw_value = TabSettings.get(key, default)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def get_ranking_settings(slug: str) -> Dict[str, object]:
    config = RANKING_TYPES[slug]
    return {
        "slug": slug,
        "label": config.label,
        "public": _get_bool(config.public_key),
        "include_speaks": _get_bool(config.include_speaks_key),
        "max_visible": _get_int(config.max_visible_key, config.default_max_visible),
    }


def get_all_ranking_settings() -> List[Dict[str, object]]:
    return [get_ranking_settings(slug) for slug in RANKING_TYPES.keys()]


def set_ranking_settings(slug: str, *, public: bool, include_speaks: bool, max_visible: int):
    config = RANKING_TYPES[slug]
    TabSettings.set(config.public_key, int(public))
    TabSettings.set(config.include_speaks_key, int(include_speaks))
    TabSettings.set(config.max_visible_key, max_visible)


def get_standings_publication_setting(slug: str) -> Dict[str, object]:
    config = STANDINGS_PUBLICATION_TYPES[slug]
    return {
        "slug": slug,
        "label": config.label,
        "published": _get_bool(config.setting_key),
    }


def get_all_standings_publication_settings() -> List[Dict[str, object]]:
    return [
        get_standings_publication_setting(slug)
        for slug in STANDINGS_PUBLICATION_TYPES.keys()
    ]


def set_standings_publication_setting(slug: str, published: bool):
    config = STANDINGS_PUBLICATION_TYPES[slug]
    TabSettings.set(config.setting_key, int(published))


def is_team_results_public() -> bool:
    return get_ranking_settings("team")["public"]


def is_speaker_results_public() -> bool:
    varsity_settings = get_ranking_settings("varsity")
    novice_settings = get_ranking_settings("novice")
    return bool(varsity_settings["public"] or novice_settings["public"])


def is_speaker_standings_published() -> bool:
    return bool(get_standings_publication_setting("speaker_results")["published"])


def is_team_standings_published() -> bool:
    return bool(get_standings_publication_setting("team_results")["published"])


def is_ballot_page_public(tot_rounds: int) -> bool:
    return any(
        get_ballot_round_settings(round_number)["visible"]
        for round_number in range(1, tot_rounds + 1)
    )


def get_all_ballot_round_settings(tot_rounds: int) -> List[Dict[str, object]]:
    return [
        get_ballot_round_settings(round_number)
        for round_number in range(1, tot_rounds + 1)
    ]


def get_ballot_round_settings(round_number: int) -> Dict[str, object]:
    return {
        "round_number": round_number,
        "visible": _get_bool(_round_key(round_number, "visible")),
        "include_speaks": _get_bool(_round_key(round_number, "include_speaks")),
        "include_ranks": _get_bool(_round_key(round_number, "include_ranks")),
    }


def set_ballot_round_settings(
    round_number: int,
    *,
    visible: bool,
    include_speaks: bool,
    include_ranks: bool,
):
    TabSettings.set(_round_key(round_number, "visible"), int(visible))
    TabSettings.set(_round_key(round_number, "include_speaks"), int(include_speaks))
    TabSettings.set(_round_key(round_number, "include_ranks"), int(include_ranks))


def _round_key(round_number: int, suffix: str) -> str:
    return f"public_ballots_round_{round_number}_{suffix}"
