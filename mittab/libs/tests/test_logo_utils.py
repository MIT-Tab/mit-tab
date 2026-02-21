import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

PIL_Image = pytest.importorskip("PIL.Image")

from mittab.apps.tab import logo_utils


def _make_uploaded_image(*, image_format="PNG", width=256, height=256):
    image = PIL_Image.new("RGB", (width, height), color=(64, 96, 160))
    data = io.BytesIO()
    image.save(data, format=image_format)
    extension = "jpg" if image_format == "JPEG" else image_format.lower()
    content_type = "image/jpeg" if image_format == "JPEG" else f"image/{extension}"
    return SimpleUploadedFile(
        f"logo.{extension}",
        data.getvalue(),
        content_type=content_type,
    )


def test_validate_tournament_logo_accepts_square_png():
    uploaded = _make_uploaded_image(image_format="PNG", width=320, height=320)
    logo_utils.validate_tournament_logo(uploaded)


def test_validate_tournament_logo_accepts_non_square_jpeg():
    uploaded = _make_uploaded_image(image_format="JPEG", width=480, height=320)
    logo_utils.validate_tournament_logo(uploaded)


def test_validate_tournament_logo_rejects_extreme_aspect_ratio():
    uploaded = _make_uploaded_image(image_format="PNG", width=640, height=200)
    with pytest.raises(ValidationError, match="aspect ratio"):
        logo_utils.validate_tournament_logo(uploaded)


def test_validate_tournament_logo_rejects_unsupported_format():
    uploaded = _make_uploaded_image(image_format="GIF", width=320, height=320)
    with pytest.raises(ValidationError, match="PNG, JPEG, or WebP"):
        logo_utils.validate_tournament_logo(uploaded)


def test_validate_tournament_logo_rejects_non_image_bytes():
    uploaded = SimpleUploadedFile(
        "logo.png",
        b"this is not an image payload",
        content_type="image/png",
    )
    with pytest.raises(ValidationError, match="real image"):
        logo_utils.validate_tournament_logo(uploaded)


def test_validate_tournament_logo_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(logo_utils, "LOGO_MAX_BYTES", 1024)
    uploaded = _make_uploaded_image(image_format="PNG", width=1200, height=1200)
    with pytest.raises(ValidationError, match="1MB or smaller"):
        logo_utils.validate_tournament_logo(uploaded)


def test_save_tournament_logo_reencodes_to_png(tmp_path, monkeypatch):
    monkeypatch.setattr(logo_utils, "LOGO_UPLOAD_DIR", str(tmp_path))
    uploaded = _make_uploaded_image(image_format="JPEG", width=640, height=320)

    logo_utils.save_tournament_logo(uploaded)

    saved_path = tmp_path / logo_utils.LOGO_FILENAME
    assert saved_path.exists()

    with PIL_Image.open(saved_path) as saved:
        assert saved.format == "PNG"
        assert saved.size == (640, 320)
