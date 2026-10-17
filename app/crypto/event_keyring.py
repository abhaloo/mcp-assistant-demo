"""Versioned AES-256-GCM keyring for execution event payload encryption."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class EventEncryptionKeyring:
    """Versioned AES-256-GCM keys injected by the deployment secret manager."""

    current_version: str
    previous_version: str | None
    _keys: dict[str, bytes]

    @classmethod
    def parse(cls, raw: str) -> EventEncryptionKeyring:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError
            current = data["current"]
            previous = data.get("previous")
            encoded_keys = data["keys"]
            if not isinstance(current, str) or not current:
                raise TypeError
            if previous is not None and (not isinstance(previous, str) or not previous):
                raise TypeError
            if previous == current:
                raise ValueError
            if not isinstance(encoded_keys, dict):
                raise TypeError
            needed = {current}
            if previous is not None:
                needed.add(previous)
            if needed != set(encoded_keys):
                raise KeyError
            keys: dict[str, bytes] = {}
            for version, encoded in encoded_keys.items():
                if not isinstance(version, str) or not isinstance(encoded, str):
                    raise TypeError
                key = base64.b64decode(encoded, validate=True)
                if len(key) != 32:
                    raise ValueError
                keys[version] = key
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, binascii.Error) as exc:
            raise ValueError("invalid event encryption keyring") from exc
        return cls(current_version=current, previous_version=previous, _keys=keys)

    def key_for(self, version: str) -> bytes:
        try:
            return self._keys[version]
        except KeyError as exc:
            raise ValueError("event encryption key version unavailable") from exc

    def __repr__(self) -> str:
        versions = sorted(self._keys)
        return (
            "EventEncryptionKeyring("
            f"current_version={self.current_version!r}, "
            f"previous_version={self.previous_version!r}, versions={versions!r})"
        )
