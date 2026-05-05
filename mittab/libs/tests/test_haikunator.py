import re
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from haikunator import Haikunator as LegacyHaikunator

from mittab.apps.tab.models import (
    BALLOT_CODE_MAX_LENGTH,
    BALLOT_CODE_TOKEN_LENGTH,
    Judge,
)
from mittab.libs.haikunator import Haikunator


def test_haikunate_preserves_zero_length_token_format():
    code = Haikunator(seed=1).haikunate(token_length=0)

    assert re.match(r"^[a-z]+-[a-z]+$", code)


def test_legacy_import_path_uses_local_haikunator():
    assert LegacyHaikunator is Haikunator


def test_haikunate_generates_configurable_readable_token():
    code = Haikunator(seed=1).haikunate(token_length=8, token_chars="ab")

    assert re.match(r"^[a-z]+-[a-z]+-[ab]{8}$", code)


def test_haikunate_supports_hex_tokens():
    code = Haikunator(seed=1).haikunate(token_length=8, token_hex=True)

    assert re.match(r"^[a-z]+-[a-z]+-[0-9a-f]{8}$", code)


def test_judge_ballot_code_validation_accepts_new_format():
    assert Judge(ballot_code="alpha-bravo").is_valid_ballot_code()
    assert Judge(ballot_code="alpha-bravo-code123").is_valid_ballot_code()
    assert Judge(ballot_code="TEST123").is_valid_ballot_code()

    with pytest.raises(ValidationError):
        Judge(ballot_code="alpha-123").is_valid_ballot_code()


def test_generated_judge_ballot_code_has_secure_readable_token():
    with mock.patch("mittab.apps.tab.models.Judge.objects.filter") as filter_mock:
        filter_mock.return_value.first.return_value = None
        judge = Judge()
        judge.set_unique_ballot_code()

    assert judge.is_valid_ballot_code()
    assert len(judge.ballot_code) <= BALLOT_CODE_MAX_LENGTH

    sections = judge.ballot_code.split("-")
    assert len(sections) == 3
    assert len(sections[2]) == BALLOT_CODE_TOKEN_LENGTH
