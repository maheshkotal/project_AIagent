from app.codes import ALPHABET, generate_code


def test_generate_code_uses_base62_alphabet():
    code = generate_code()
    assert len(code) == 7
    assert set(code) <= set(ALPHABET)


def test_generate_code_respects_length():
    assert len(generate_code(10)) == 10
