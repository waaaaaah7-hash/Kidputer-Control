"""Drives, folder sizes and the file explorer (roadmap #19)."""

from app.routers.files import folder_changes

MB = 1024 * 1024


def tree(games_mb: int, downloads_mb: int, extra: dict | None = None) -> dict:
    users = {"n": "Users", "s": downloads_mb * MB, "c": [{"n": "Sam", "s": downloads_mb * MB, "c": [{"n": "Downloads", "s": downloads_mb * MB}]}]}
    games = {"n": "Games", "s": games_mb * MB, "c": [{"n": "Minecraft", "s": games_mb * MB}]}
    children = [games, users] + ([extra] if extra else [])
    return {"tree": {"n": "C:\\", "s": sum(c["s"] for c in children), "f": 10, "d": 5, "c": children}, "largest": [], "by_type": {}}


def test_drives_and_scans(admin_client, agent):
    dev = agent.device_id
    drives = {"drives": [{"name": "C:\\", "label": "Windows", "type": "Fixed", "size": 500 * 1024 * MB, "free": 120 * 1024 * MB},
                         {"name": "E:\\", "label": "SANDISK", "type": "Removable", "size": 32 * 1024 * MB, "free": 1024 * MB}],
              "devices": ["Sam's iPhone"]}
    assert agent.post("/api/v1/agent/reports/drives", json=drives).status_code == 204
    r = admin_client.get(f"/api/v1/devices/{dev}/drives").json()
    assert [d["name"] for d in r["drives"]] == ["C:\\", "E:\\"] and r["devices"] == ["Sam's iPhone"] and r["scans"] == {}
    assert admin_client.get(f"/api/v1/devices/{dev}/scans/C").status_code == 404

    assert agent.post("/api/v1/agent/reports/scan:C", json=tree(1000, 200)).status_code == 204
    r = admin_client.get(f"/api/v1/devices/{dev}/drives").json()
    assert r["scans"]["C"]["size"] == 1200 * MB
    first = admin_client.get(f"/api/v1/devices/{dev}/scans/c").json()
    assert first["scan"]["tree"]["c"][0]["n"] == "Games" and first["changes"] == [] and first["previous_scanned_at"] is None

    # A week later: Downloads grew by 12 GB, a new game appeared, Minecraft barely changed.
    agent.post("/api/v1/agent/reports/scan:C", json=tree(1010, 200 + 12 * 1024, {"n": "NewGame", "s": 3000 * MB}))
    second = admin_client.get(f"/api/v1/devices/{dev}/scans/C").json()
    assert second["previous_scanned_at"] is not None
    assert [(c["path"], c["size"] - (c["previous"] or 0)) for c in second["changes"]] == [
        ("C:\\Users\\Sam\\Downloads", 12 * 1024 * MB),
        ("C:\\NewGame", 3000 * MB),
    ]


def test_scan_reports_are_checked(agent):
    assert agent.post("/api/v1/agent/reports/scan:CC", json={}).status_code == 404
    assert agent.post("/api/v1/agent/reports/scan:1", json={}).status_code == 404


def test_scan_and_list_commands(admin_client, agent):
    dev = agent.device_id
    r = admin_client.post(f"/api/v1/devices/{dev}/scans/d")
    assert r.status_code == 201 and r.json()["payload"] == {"drive": "D"}
    assert admin_client.post(f"/api/v1/devices/{dev}/scans/DD").status_code == 422
    r = admin_client.post(f"/api/v1/devices/{dev}/files/list", json={"path": "C:/Users/Sam/Downloads"})
    assert r.status_code == 201 and r.json()["payload"] == {"path": "C:\\Users\\Sam\\Downloads"}
    for bad in ("\\\\server\\share", "Users\\Sam", "C:\\Users\\..\\Windows", "x"):
        assert admin_client.post(f"/api/v1/devices/{dev}/files/list", json={"path": bad}).status_code == 422, bad
    kinds = [c["kind"] for c in agent.get("/api/v1/agent/state").json()["commands"]]
    assert kinds == ["scan_drive", "list_folder"]


def test_scan_schedule_setting(admin_client, agent):
    assert agent.get("/api/v1/agent/state").json()["scan_every_days"] == 7
    admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"scan_every_days": 0})
    assert agent.get("/api/v1/agent/state").json()["scan_every_days"] == 0
    assert admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"scan_every_days": 365}).status_code == 422


def test_changes_need_a_previous_scan_and_skip_small_growth():
    assert folder_changes(tree(1, 1), None) == []
    assert folder_changes(tree(10, 10), tree(10, 9)) == []  # 1 MB: too small to mention
