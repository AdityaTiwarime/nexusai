"""Encryption of secrets at rest (connector credentials, webhook secrets).

Fernet = AES-128-CBC + HMAC-SHA256 with a random IV per value, so tampered or
foreign ciphertext is rejected rather than decrypted into garbage. The key comes
from NEXUS_ENCRYPTION_KEY and never touches the database.

Rotating the key: decrypt with the old key and re-encrypt with the new one in a
maintenance script (MultiFernet supports reading with several keys).
"""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, data: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken:
            raise ValueError("Stored secret can't be decrypted with the current key") from None
