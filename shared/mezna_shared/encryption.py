"""
Fernet symmetric encryption for credential storage.

CRITICAL RULES:
1. The encryption key (CREDENTIAL_ENCRYPTION_KEY) must be stored only in
   environment variables or Coolify secrets. Never in code or git.
2. Losing the encryption key means losing access to all stored credentials.
   Back it up separately from the database.
3. Never log, return in API responses, or print decrypted values.
4. If the key is compromised, rotate it: re-encrypt all credentials with a new key.

Key generation (run once, store result in .env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import structlog
from cryptography.fernet import Fernet, InvalidToken

log = structlog.get_logger()


class CredentialEncryption:
    """
    Fernet-based encryption for API credentials.

    Fernet guarantees:
    - AES-128-CBC encryption
    - HMAC-SHA256 authentication (tamper detection)
    - Timestamp embedded (allows enforcing max age if needed later)
    """

    def __init__(self, encryption_key: str) -> None:
        """
        Initialise with a Fernet key.

        Args:
            encryption_key: Base64-encoded 32-byte Fernet key.
                           Generate with: Fernet.generate_key().decode()

        Raises:
            ValueError: If the key is invalid.
        """
        if not encryption_key:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY is required. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        try:
            key_bytes = (
                encryption_key.encode()
                if isinstance(encryption_key, str)
                else encryption_key
            )
            self._fernet = Fernet(key_bytes)
        except Exception as exc:
            raise ValueError(
                f"Invalid CREDENTIAL_ENCRYPTION_KEY: {exc}. "
                "Key must be a valid Fernet key (44-character base64 string)."
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext credential value.

        Returns the encrypted value as a string suitable for DB storage.
        Safe to log the output (it's ciphertext).
        """
        if not plaintext:
            return ""
        encrypted_bytes = self._fernet.encrypt(plaintext.encode("utf-8"))
        return encrypted_bytes.decode("utf-8")

    def decrypt(self, encrypted_value: str) -> str:
        """
        Decrypt a stored credential value.

        Returns the plaintext credential.
        NEVER log the return value of this method.

        Raises:
            InvalidToken: If the value has been tampered with or the key is wrong.
        """
        if not encrypted_value:
            return ""
        try:
            decrypted_bytes = self._fernet.decrypt(encrypted_value.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as exc:
            log.error(
                "encryption.decrypt_failed",
                reason="InvalidToken — key mismatch or tampered ciphertext",
            )
            raise

    def is_valid_ciphertext(self, value: str) -> bool:
        """Check if a value is valid Fernet ciphertext without decrypting fully."""
        try:
            self._fernet.decrypt(value.encode("utf-8"))
            return True
        except (InvalidToken, Exception):
            return False


def generate_key() -> str:
    """Generate a new Fernet encryption key. Run once and store in .env."""
    return Fernet.generate_key().decode("utf-8")


def mask_credential(value: str, visible_chars: int = 4) -> str:
    """
    Mask a credential for display purposes.
    Returns e.g. "vtci...gjsgD" for a 64-char key.
    ONLY use this for display — never for comparison.
    """
    if not value or len(value) <= visible_chars * 2:
        return "***"
    return f"{value[:visible_chars]}...{value[-visible_chars:]}"
