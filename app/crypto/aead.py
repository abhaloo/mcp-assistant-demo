"""Authenticated Encryption with Associated Data (AEAD) helper functions."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from app.crypto.event_keyring import EventEncryptionKeyring

_NONCE_SIZE_BYTES = 12


def encrypt_bytes(
    keyring: EventEncryptionKeyring,
    plaintext: bytes,
    aad: bytes | None = None,
) -> tuple[str, bytes, bytes]:
    """Encrypt plaintext using the current key in the keyring.

    Returns:
        tuple of (key_version, nonce, ciphertext).
    """
    key_version = keyring.current_version
    key = keyring.key_for(key_version)
    nonce = os.urandom(_NONCE_SIZE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return key_version, nonce, ciphertext


def decrypt_bytes(
    keyring: EventEncryptionKeyring,
    key_version: str,
    nonce: bytes,
    ciphertext: bytes,
    aad: bytes | None = None,
) -> bytes:
    """Decrypt ciphertext using the specified key version from the keyring.

    Raises:
        ValueError: If the key version is not in the keyring.
        InvalidTag: If decryption authentication fails.
    """
    key = keyring.key_for(key_version)
    return AESGCM(key).decrypt(nonce, ciphertext, aad)
