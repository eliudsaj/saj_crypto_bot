from app import app


def headers(role):
    return {
        "X-Nexus-User-Id": f"{role}-user",
        "X-Nexus-Tenant-Id": "default",
        "X-Nexus-Role": role,
        "X-Nexus-Email": f"{role}@nexus.local",
    }


def test_viewer_cannot_start_bot_or_save_config(monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "true")
    client = app.test_client()

    start = client.post("/api/bot/start", json={}, headers=headers("viewer"))
    assert start.status_code == 403

    config = client.post("/api/config", json={}, headers=headers("viewer"))
    assert config.status_code == 403


def test_trader_can_access_trade_route_but_not_user_admin(monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "true")
    client = app.test_client()

    stop = client.post("/api/bot/stop", headers=headers("trader"))
    assert stop.status_code != 403

    users = client.get("/api/users", headers=headers("trader"))
    assert users.status_code == 403


def test_admin_can_list_users_and_auth_me_includes_permissions(monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "true")
    client = app.test_client()

    me = client.get("/api/auth/me", headers=headers("admin"))
    assert me.status_code == 200
    assert "users" in me.json["data"]["permissions"]

    users = client.get("/api/users", headers=headers("admin"))
    assert users.status_code == 200
    assert users.json["status"] == "success"
