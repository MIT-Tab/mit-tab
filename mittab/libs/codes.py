from mittab.libs.haikunator import Haikunator


HUMAN_READABLE_TOKEN_CHARS = "23456789abcdefghijkmnopqrstuvwxyz"
READABLE_AUTH_CODE_TOKEN_LENGTH = 6


def generate_readable_auth_code(exists):
    """Generate a unique word-word-code auth token."""
    haikunator = Haikunator()
    code = haikunator.haikunate(
        token_length=READABLE_AUTH_CODE_TOKEN_LENGTH,
        token_chars=HUMAN_READABLE_TOKEN_CHARS,
    )

    while exists(code):
        code = haikunator.haikunate(
            token_length=READABLE_AUTH_CODE_TOKEN_LENGTH,
            token_chars=HUMAN_READABLE_TOKEN_CHARS,
        )

    return code
