from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
import base64
import json
import re

import win32crypt

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


_MAGIC = b"OBV1"
_NONCE_BYTES = 12


class VaultIntegrityError(ValueError):
    pass


class OnboardingVault:
    """AES-GCM envelope encryption with isolated per-school data keys."""

    def __init__(self, organization_master_key: bytes) -> None:
        key = bytes(organization_master_key)
        if len(key) != 32:
            raise ValueError("Organization master key must be 32 bytes.")
        self._master_key: bytes | None = key

    @property
    def is_locked(self) -> bool:
        return self._master_key is None

    def lock(self) -> None:
        self._master_key = None

    def encrypt(self, school: str, plaintext: bytes, *, context: str) -> bytes:
        clean_school = self._required(school, "School")
        clean_context = self._required(context, "Encryption context")
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._school_key(clean_school)).encrypt(
            nonce,
            bytes(plaintext),
            self._associated_data(clean_school, clean_context),
        )
        return _MAGIC + nonce + ciphertext

    def decrypt(self, school: str, envelope: bytes, *, context: str) -> bytes:
        clean_school = self._required(school, "School")
        clean_context = self._required(context, "Encryption context")
        payload = bytes(envelope)
        if not payload.startswith(_MAGIC) or len(payload) <= len(_MAGIC) + _NONCE_BYTES:
            raise VaultIntegrityError("Encrypted onboarding payload is invalid.")
        nonce_start = len(_MAGIC)
        nonce_end = nonce_start + _NONCE_BYTES
        try:
            return AESGCM(self._school_key(clean_school)).decrypt(
                payload[nonce_start:nonce_end],
                payload[nonce_end:],
                self._associated_data(clean_school, clean_context),
            )
        except InvalidTag as exc:
            raise VaultIntegrityError("Encrypted onboarding payload failed authentication.") from exc

    def cache_for_device(self, cache_path: Path) -> None:
        if self._master_key is None:
            raise VaultIntegrityError("Onboarding vault is locked.")
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        protected = win32crypt.CryptProtectData(
            self._master_key,
            "Launch Pad Learning Onboarding organization key cache",
            None,
            None,
            None,
            0,
        )
        self._write_bytes_atomic(path, protected)

    @classmethod
    def from_device_cache(cls, cache_path: Path) -> OnboardingVault:
        path = Path(cache_path)
        try:
            _description, key = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)
        except Exception as exc:
            raise VaultIntegrityError("Onboarding device cache could not be unlocked for this Windows user.") from exc
        return cls(key)

    @staticmethod
    def forget_device(cache_path: Path) -> None:
        Path(cache_path).unlink(missing_ok=True)

    @staticmethod
    def _write_bytes_atomic(path: Path, payload: bytes) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _school_key(self, school: str) -> bytes:
        if self._master_key is None:
            raise VaultIntegrityError("Onboarding vault is locked.")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"LaunchPad-Onboarding-School-Key-v1",
            info=school.casefold().encode("utf-8"),
        ).derive(self._master_key)

    @staticmethod
    def _associated_data(school: str, context: str) -> bytes:
        return f"onboarding-v1\0{school.casefold()}\0{context}".encode("utf-8")

    @staticmethod
    def _required(value: str, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError(f"{label} is required.")
        return clean


class OnboardingKeyring:
    """Shared passphrase/recovery-wrapped organization keyring."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def create(cls, path: Path, *, passphrase: str, recovery_key: str) -> OnboardingVault:
        target = Path(path)
        if target.exists():
            raise ValueError("Onboarding keyring already exists.")
        if len(str(passphrase or "")) < 12:
            raise ValueError("Onboarding passphrase must be at least 12 characters.")
        if len(str(recovery_key or "")) < 12:
            raise ValueError("Onboarding recovery key must be at least 12 characters.")
        organization_key = os.urandom(32)
        payload = {
            "kind": "onboarding_keyring",
            "version": 1,
            "passphrase": cls._wrap_key(organization_key, str(passphrase), "passphrase"),
            "recovery": cls._wrap_key(organization_key, str(recovery_key), "recovery"),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        cls._write_json_atomic(target, payload)
        return OnboardingVault(organization_key)

    def unlock_with_passphrase(self, passphrase: str) -> OnboardingVault:
        return OnboardingVault(self._unwrap_key("passphrase", str(passphrase or "")))

    def unlock_with_recovery_key(self, recovery_key: str) -> OnboardingVault:
        return OnboardingVault(self._unwrap_key("recovery", str(recovery_key or "")))

    def _unwrap_key(self, slot: str, secret: str) -> bytes:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("kind") != "onboarding_keyring" or payload.get("version") != 1:
                raise ValueError("Unsupported onboarding keyring format.")
            wrapped = payload[slot]
            salt = base64.b64decode(wrapped["salt"], validate=True)
            nonce = base64.b64decode(wrapped["nonce"], validate=True)
            ciphertext = base64.b64decode(wrapped["ciphertext"], validate=True)
            wrapping_key = self._derive_wrapping_key(secret, salt)
            return AESGCM(wrapping_key).decrypt(nonce, ciphertext, f"onboarding-keyring-v1:{slot}".encode())
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, InvalidTag) as exc:
            raise VaultIntegrityError("Onboarding keyring could not be unlocked.") from exc

    @classmethod
    def _wrap_key(cls, organization_key: bytes, secret: str, slot: str) -> dict[str, str]:
        salt = os.urandom(16)
        nonce = os.urandom(_NONCE_BYTES)
        wrapping_key = cls._derive_wrapping_key(secret, salt)
        ciphertext = AESGCM(wrapping_key).encrypt(
            nonce,
            organization_key,
            f"onboarding-keyring-v1:{slot}".encode(),
        )
        return {
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    @staticmethod
    def _derive_wrapping_key(secret: str, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(secret.encode("utf-8"))

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as file:
                file.write(serialized)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class EncryptedArtifactVault:
    """Encrypted file storage with contained short-lived decrypted temp copies."""

    def __init__(self, root: Path, temp_root: Path, *, vault: OnboardingVault) -> None:
        self.root = Path(root).resolve()
        self.temp_root = Path(temp_root).resolve()
        self.vault = vault
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def seal_file(self, school: str, source_path: Path, *, artifact_id: str) -> Path:
        artifact = self._artifact_id(artifact_id)
        source = Path(source_path).resolve(strict=True)
        if not source.is_file() or source.is_symlink():
            raise ValueError("Encrypted artifact source must be a regular file.")
        envelope = self.vault.encrypt(
            school,
            source.read_bytes(),
            context=f"artifact:{artifact}",
        )
        destination = self.root / f"{artifact}.obv"
        if destination.exists():
            raise ValueError("Encrypted artifact already exists.")
        OnboardingVault._write_bytes_atomic(destination, envelope)
        return destination

    def open_temp(
        self,
        school: str,
        sealed_path: Path,
        *,
        artifact_id: str,
        suffix: str,
    ) -> Path:
        artifact = self._artifact_id(artifact_id)
        sealed = Path(sealed_path).resolve(strict=True)
        if self.root not in sealed.parents or not sealed.is_file():
            raise ValueError("Encrypted artifact is outside the vault.")
        clean_suffix = str(suffix or "").casefold()
        if clean_suffix not in {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}:
            raise ValueError("Encrypted artifact temp suffix is not allowed.")
        plaintext = self.vault.decrypt(
            school,
            sealed.read_bytes(),
            context=f"artifact:{artifact}",
        )
        target = self.temp_root / f"{artifact}-{uuid4().hex}{clean_suffix}"
        OnboardingVault._write_bytes_atomic(target, plaintext)
        return target

    def cleanup_temp(self, path: Path) -> None:
        target = Path(path).resolve()
        if self.temp_root not in target.parents:
            raise ValueError("Temp artifact is outside the onboarding temp vault.")
        target.unlink(missing_ok=True)

    def cleanup_stale(self) -> int:
        removed = 0
        for path in self.temp_root.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    @staticmethod
    def _artifact_id(value: str) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", clean):
            raise ValueError("Artifact ID contains unsafe characters.")
        return clean


def load_or_create_device_vault(key_path: Path) -> OnboardingVault:
    """Load a DPAPI-wrapped organization key or create one for this device user."""
    path = Path(key_path)
    if path.exists():
        if not path.is_file():
            raise ValueError("Onboarding device key path must be a file.")
        return OnboardingVault.from_device_cache(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    vault = OnboardingVault(key)
    try:
        vault.cache_for_device(path)
    except OSError:
        if not path.is_file():
            raise
        return OnboardingVault.from_device_cache(path)
    return vault
