from __future__ import annotations

import pytest

from onboarding_vault import EncryptedArtifactVault, OnboardingKeyring, OnboardingVault, VaultIntegrityError


def test_vault_encrypts_per_school_and_rejects_tamper_or_wrong_school():
    vault = OnboardingVault(bytes(range(32)))
    encrypted = vault.encrypt("Palmdale", b"sensitive onboarding value", context="employee.email")

    assert b"sensitive onboarding value" not in encrypted
    assert vault.decrypt("Palmdale", encrypted, context="employee.email") == b"sensitive onboarding value"
    with pytest.raises(VaultIntegrityError):
        vault.decrypt("Hawthorne", encrypted, context="employee.email")
    with pytest.raises(VaultIntegrityError):
        vault.decrypt("Palmdale", encrypted[:-1] + bytes([encrypted[-1] ^ 1]), context="employee.email")


def test_shared_keyring_unlocks_on_another_computer_with_passphrase(tmp_path):
    keyring_path = tmp_path / "shared" / "onboarding.keyring.json"
    created = OnboardingKeyring.create(
        keyring_path,
        passphrase="correct horse battery staple",
        recovery_key="LPL-RECOVERY-KEY-2026",
    )
    payload = created.encrypt("Palmdale", b"school data", context="test")

    unlocked_elsewhere = OnboardingKeyring(keyring_path).unlock_with_passphrase(
        "correct horse battery staple"
    )

    assert unlocked_elsewhere.decrypt("Palmdale", payload, context="test") == b"school data"
    with pytest.raises(VaultIntegrityError):
        OnboardingKeyring(keyring_path).unlock_with_passphrase("wrong passphrase")


def test_shared_keyring_can_use_and_forget_windows_device_cache(tmp_path):
    vault = OnboardingKeyring.create(
        tmp_path / "shared.keyring.json",
        passphrase="correct horse battery staple",
        recovery_key="LPL-RECOVERY-KEY-2026",
    )
    cache_path = tmp_path / "device.dpapi"

    vault.cache_for_device(cache_path)
    cached = OnboardingVault.from_device_cache(cache_path)
    cached.forget_device(cache_path)

    encrypted = vault.encrypt("Palmdale", b"school data", context="test")
    assert cached.decrypt("Palmdale", encrypted, context="test") == b"school data"
    assert not cache_path.exists()


def test_lock_clears_in_memory_key_but_preserves_device_cache_for_restart(tmp_path):
    cache_path = tmp_path / "device.dpapi"
    vault = OnboardingVault(b"l" * 32)
    vault.cache_for_device(cache_path)
    encrypted = vault.encrypt("Palmdale", b"school data", context="test")

    vault.lock()

    assert vault.is_locked is True
    assert cache_path.exists()
    with pytest.raises(VaultIntegrityError, match="locked"):
        vault.decrypt("Palmdale", encrypted, context="test")
    restarted = OnboardingVault.from_device_cache(cache_path)
    assert restarted.decrypt("Palmdale", encrypted, context="test") == b"school data"


def test_encrypted_artifact_vault_opens_short_lived_temp_and_cleans_stale_files(tmp_path):
    vault = OnboardingVault(b"v" * 32)
    artifacts = EncryptedArtifactVault(tmp_path / "encrypted", tmp_path / "temp", vault=vault)
    source = tmp_path / "filled.pdf"
    source.write_bytes(b"%PDF-1.4\nprivate filled form")

    sealed = artifacts.seal_file("Palmdale", source, artifact_id="package-1")
    assert b"private filled form" not in sealed.read_bytes()
    opened = artifacts.open_temp("Palmdale", sealed, artifact_id="package-1", suffix=".pdf")
    assert opened.read_bytes() == source.read_bytes()
    artifacts.cleanup_temp(opened)
    assert not opened.exists()

    stale = artifacts.temp_root / "stale.pdf"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    assert artifacts.cleanup_stale() == 1
