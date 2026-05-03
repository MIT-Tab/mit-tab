import io
import os
import warnings

from django.core.exceptions import ValidationError

from mittab import settings

LOGO_UPLOAD_DIR = os.path.join(settings.BASE_DIR, "mittab", "uploads")
LOGO_FILENAME = "tournament_logo.png"
LOGO_MAX_BYTES = 8 * 1024 * 1024
LOGO_MAX_WIDTH = 3000
LOGO_MAX_HEIGHT = 3000
LOGO_MAX_PIXELS = 9_000_000
LOGO_MIN_ASPECT_RATIO = 0.5
LOGO_MAX_ASPECT_RATIO = 2.0
LOGO_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
LOGO_ALLOWED_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")


def _get_pillow():
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise ValidationError("Image uploads require Pillow.") from exc
    return Image, UnidentifiedImageError


def _open_and_decode_image(image_bytes):
    Image, UnidentifiedImageError = _get_pillow()

    try:
        image_stream = io.BytesIO(image_bytes)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(image_stream)
            image.verify()

        image_stream = io.BytesIO(image_bytes)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(image_stream)
            image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise ValidationError("Logo must be a real image file.") from exc

    return image


def _validate_and_open_tournament_logo(uploaded_file):
    if getattr(uploaded_file, "size", 0) > LOGO_MAX_BYTES:
        max_mb = max(1, round(LOGO_MAX_BYTES / (1024 * 1024)))
        raise ValidationError(f"Logo must be {max_mb}MB or smaller.")

    uploaded_file.seek(0)
    image_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    image = _open_and_decode_image(image_bytes)

    image_format = (image.format or "").upper()
    if image_format not in LOGO_ALLOWED_FORMATS:
        image.close()
        raise ValidationError("Logo must be PNG, JPEG, or WebP.")

    if getattr(image, "is_animated", False):
        image.close()
        raise ValidationError("Animated logos are not supported.")

    width, height = image.size
    if width <= 0 or height <= 0:
        image.close()
        raise ValidationError("Logo has invalid dimensions.")

    if width > LOGO_MAX_WIDTH or height > LOGO_MAX_HEIGHT:
        image.close()
        raise ValidationError(
            f"Logo dimensions must be at most {LOGO_MAX_WIDTH}x{LOGO_MAX_HEIGHT}."
        )

    if width * height > LOGO_MAX_PIXELS:
        image.close()
        raise ValidationError("Logo resolution is too large.")

    aspect_ratio = width / float(height)
    if not LOGO_MIN_ASPECT_RATIO <= aspect_ratio <= LOGO_MAX_ASPECT_RATIO:
        image.close()
        raise ValidationError("Logo aspect ratio must be between 1:2 and 2:1.")

    return image


def validate_tournament_logo(uploaded_file):
    image = _validate_and_open_tournament_logo(uploaded_file)
    image.close()
    uploaded_file.seek(0)


def save_tournament_logo(uploaded_file):
    image = _validate_and_open_tournament_logo(uploaded_file)
    converted_image = None

    os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
    path = get_tournament_logo_abs_path(existing_only=False)
    try:
        image_to_save = image
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        if image.mode not in ("RGB", "RGBA"):
            image_to_save = image.convert("RGBA" if has_alpha else "RGB")
            converted_image = image_to_save

        image_to_save.save(path, format="PNG", optimize=True)
    finally:
        if converted_image is not None:
            converted_image.close()
        image.close()
        uploaded_file.seek(0)

    return LOGO_FILENAME


def get_tournament_logo_abs_path(existing_only=True):
    path = os.path.join(LOGO_UPLOAD_DIR, LOGO_FILENAME)
    if existing_only and not os.path.exists(path):
        return None
    return path


def has_tournament_logo():
    return get_tournament_logo_abs_path(existing_only=True) is not None


def get_tournament_logo_mtime():
    logo_path = get_tournament_logo_abs_path(existing_only=True)
    if not logo_path:
        return None
    return int(os.path.getmtime(logo_path))
