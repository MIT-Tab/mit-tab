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


def _hex_to_rgb_tuple(value):
    normalized = normalize_theme_color(value)
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _hex_to_rgb_csv(value):
    red, green, blue = _hex_to_rgb_tuple(value)
    return f"{red}, {green}, {blue}"


def _pick_contrast_text(theme_color):
    """Pick a grayscale text color with enough contrast against the theme.

    Uses YIQ perceived-brightness with the standard 128 midpoint. Themes
    weighted toward bright (yellow, cyan, lime, white) get near-black text;
    everything else gets white. The raw theme color is used because the
    navbar's color-mix(85%, black) shading rarely flips the bucket and a
    consistent answer here matches secondary surfaces that don't shade.
    """
    rgb = _hex_to_rgb_tuple(theme_color)
    yiq = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
    if yiq >= 128:
        return ("#1a1a1a", "26, 26, 26")
    return ("#ffffff", "255, 255, 255")


def get_theme_css_variables(theme_color):
    normalized = normalize_theme_color(theme_color)
    rgb_csv = _hex_to_rgb_csv(normalized)
    on_hex, on_rgb = _pick_contrast_text(normalized)
    return (
        f"--theme-color: {normalized}; "
        f"--theme-color-rgb: {rgb_csv}; "
        f"--theme-on-color: {on_hex}; "
        f"--theme-on-color-rgb: {on_rgb};"
    )
