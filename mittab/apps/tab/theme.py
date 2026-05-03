import re

DEFAULT_THEME_COLOR = "#00438A"
HEX_COLOR_REGEX = re.compile(r"^#([0-9a-fA-F]{6})$")


def normalize_theme_color(value, default=DEFAULT_THEME_COLOR):
    if not isinstance(value, str):
        return default

    candidate = value.strip()
    if not candidate:
        return default
    if not candidate.startswith("#"):
        candidate = f"#{candidate}"

    if not HEX_COLOR_REGEX.fullmatch(candidate):
        return default
    return candidate.upper()


def _hex_to_rgb_csv(value):
    normalized = normalize_theme_color(value)
    red = int(normalized[1:3], 16)
    green = int(normalized[3:5], 16)
    blue = int(normalized[5:7], 16)
    return f"{red}, {green}, {blue}"


def get_theme_css_variables(theme_color):
    normalized = normalize_theme_color(theme_color)
    rgb_csv = _hex_to_rgb_csv(normalized)
    return f"--theme-color: {normalized}; --theme-color-rgb: {rgb_csv};"
