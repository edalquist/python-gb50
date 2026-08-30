"""Cryptographic cipher and authentication utilities for Mitsubishi GB-50."""

import random
from typing import Tuple


def create_key() -> str:
    """Generate a random 5-digit key conforming to Mitsubishi Crypt.createKey()."""
    num = random.randint(0, 9999)
    last_digit = 48 + int(4.0 * random.random()) + 1
    return f"{num:04d}{chr(last_digit)}"


def _char_to_val(c: str) -> int:
    if 'a' <= c <= 'z':
        return ord(c) - 97 + 1
    elif 'A' <= c <= 'Z':
        return ord(c) - 65 + 27
    elif '0' <= c <= '9':
        return ord(c) - 48 + 53
    raise ValueError(f"Character '{c}' is outside the valid alphanumeric cipher set")


def _val_to_char(v: int) -> str:
    if v < 27:
        return chr(97 + v - 1)
    elif v < 53:
        return chr(65 + v - 27)
    else:
        return chr(48 + v - 53)


def encrypt(plaintext: str, key_str: str) -> str:
    """Encrypt a string using the Mitsubishi 5-digit cipher algorithm.
    
    Args:
        plaintext: String to encrypt (e.g. 'UserList' or password).
        key_str: 5-digit integer string key.
        
    Returns:
        Encrypted ciphertext string.
    """
    n = int(key_str)
    n2 = n % 10
    n3 = (n // 10) % 10
    n4 = (n // 100) % 10
    n5 = (n // 1000) % 10
    n6 = n // 10000
    n7 = abs((n6 + n5) % 10 - (n4 + n3) % 10)
    
    # Generate n7 random dummy lowercase letters
    dummy = ''.join(chr(97 + random.randint(0, 25)) for _ in range(n7))
    
    out = []
    n9 = 0
    for idx, c in enumerate(plaintext):
        if idx + 1 == n2:
            out.append(dummy)
        n10 = _char_to_val(c)
        n11 = n10 + n7 + n9
        while n11 > 62:
            n11 -= 62
        out.append(_val_to_char(n11))
        n9 = n11
    if len(plaintext) + 1 == n2:
        out.append(dummy)
    return ''.join(out)


def decrypt(ciphertext: str, key: int) -> str:
    """Decrypt a ciphertext string using the Mitsubishi 5-digit cipher algorithm.
    
    Args:
        ciphertext: Encrypted string received from the controller.
        key: Integer key used during encryption.
        
    Returns:
        Decrypted plaintext string.
    """
    n2 = key % 10
    n3 = (key // 10) % 10
    n4 = (key // 100) % 10
    n5 = (key // 1000) % 10
    n6 = key // 10000
    n8 = abs((n6 + n5) % 10 - (n4 + n3) % 10)
    
    out = []
    idx = 0
    n9 = 0
    while idx < len(ciphertext):
        if idx + 1 == n2:
            idx += n8
            if idx >= len(ciphertext):
                break
        c = ciphertext[idx]
        n11 = _char_to_val(c)
        n12 = n11 - n8 - n9
        while n12 < 1:
            n12 += 62
        out.append(_val_to_char(n12))
        n9 = n11
        idx += 1
    return ''.join(out)
