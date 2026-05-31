from datetime import timedelta

from licensing.manager import LicenseManager, utc_now


def test_license_create_and_first_activation(tmp_path, monkeypatch):
    monkeypatch.delenv("LICENSE_KEY", raising=False)
    manager = LicenseManager(tmp_path / "licenses.db")
    created = manager.create_license("Acme", "ops@example.com")

    result = manager.activate(created["license_key"], machine_id="machine-a")

    assert result.valid is True
    assert result.trading_allowed is True
    activated = manager.get(created["license_key"])
    assert activated["activated_at"]
    assert activated["expires_at"]
    assert activated["machine_id"] == "machine-a"


def test_license_rejects_other_machine(tmp_path):
    manager = LicenseManager(tmp_path / "licenses.db")
    created = manager.create_license("Acme", "ops@example.com")
    assert manager.activate(created["license_key"], machine_id="machine-a").valid

    result = manager.activate(created["license_key"], machine_id="machine-b")

    assert result.valid is False
    assert result.status == "machine_mismatch"


def test_license_grace_period_allows_trading(tmp_path, monkeypatch):
    monkeypatch.setenv("LICENSE_GRACE_DAYS", "7")
    manager = LicenseManager(tmp_path / "licenses.db")
    created = manager.create_license(
        "Acme",
        "ops@example.com",
        expires_at=(utc_now() - timedelta(days=2)).isoformat(),
    )
    manager.activate(created["license_key"], machine_id="machine-a")

    result = manager.validate(created["license_key"], machine_id="machine-a")

    assert result.status == "grace"
    assert result.trading_allowed is True


def test_license_expired_after_grace_blocks_trading(tmp_path, monkeypatch):
    monkeypatch.setenv("LICENSE_GRACE_DAYS", "7")
    manager = LicenseManager(tmp_path / "licenses.db")
    created = manager.create_license(
        "Acme",
        "ops@example.com",
        expires_at=(utc_now() - timedelta(days=10)).isoformat(),
    )
    manager.activate(created["license_key"], machine_id="machine-a")

    result = manager.validate(created["license_key"], machine_id="machine-a")

    assert result.status == "expired"
    assert result.trading_allowed is False


def test_revoke_extend_and_reset_machine(tmp_path):
    manager = LicenseManager(tmp_path / "licenses.db")
    created = manager.create_license("Acme", "ops@example.com")
    manager.activate(created["license_key"], machine_id="machine-a")

    revoked = manager.revoke(created["license_key"])
    assert revoked["is_active"] is False
    assert manager.validate(created["license_key"], machine_id="machine-a").status == "revoked"

    extended = manager.extend(created["license_key"], days=30)
    assert extended["is_active"] is True
    reset = manager.reset_machine(created["license_key"])
    assert reset["machine_id"] is None

