"""Short-code generation."""

import secrets

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
DEFAULT_LENGTH = 7


def generate_code(length: int = DEFAULT_LENGTH) -> str:
    """Random base62 code; 62^7 ~ 3.5e12 combinations keeps collisions rare."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
