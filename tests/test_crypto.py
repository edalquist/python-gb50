"""Unit tests for Mitsubishi Crypt cipher and password decryption."""

import pytest
from gb50.crypto import encrypt, decrypt, create_key


def test_crypto_round_trip():
    # Mitsubishi Crypt algorithm is designed for alphanumeric tokens
    texts = ["UserList", "SampleA7", "SampleB8", "SampleC9", "ExampleFacility", "Password123", "GB50Controller"]
    for text in texts:
        # Generate 5-digit key
        key_str = create_key()
        key_int = int(key_str)
        
        # Encrypt
        encrypted = encrypt(text, key_str)
        assert encrypted != text
        
        # Decrypt
        decrypted = decrypt(encrypted, key_int)
        assert decrypted == text


def test_decode_synthetic_passwords():
    # Synthetic known-answer vectors; no controller credentials.
    assert decrypt("fgowW1iCS1wy", 12341) == "SampleA7"
    assert decrypt("Wxipd1iCS1xA", 23452) == "SampleB8"
    assert decrypt("Y5yyzdeaoK2dMS", 34563) == "SampleC9"


def test_create_key_format():
    for _ in range(50):
        key = create_key()
        assert len(key) == 5
        assert key.isdigit()
