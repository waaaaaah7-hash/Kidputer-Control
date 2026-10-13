"""App schedules (roadmap #3), custom apps (#4) and usage time (#5)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.state import expand_weekly

RULES = "/api/v1/devices/{}/app-rules"


def put_rules(admin_client, dev, rules):
    return admin_client.put(RULES.format(dev), json={"rules": rules})


def test_catalog_lists_common_apps_by_category(admin_client):
    c = admin_client.get("/api/v1/apps/catalog").json()
    assert c["categories"]["games"] == "Games"
    ids = {a["id"]: a for a in c["apps"]}
    assert ids["roblox"]["category"] == "games" and ids["chrome"]["category"] == "browsers"
    assert any(m.get("cmdline") == "net.minecraft" for m in ids["minecraft_java"]["matchers"])


def test_rules_reach_the_pc_with_allowed_times_in_utc(admin_client, agent):
    dev = agent.device_id
    rules = [
        {"target_kind": "category", "target": "games", "mode": "schedule",
         "windows": [{"day": d, "start": "16:00", "end": "19:00"} for d in range(5)] + [{"day": d, "start": "10:00", "end": "20:00"} for d in (5, 6)]},
        {"target_kind": "app", "target": "minecraft_java", "mode": "allow"},
        {"target_kind": "app", "target": "tiktok", "mode": "block"},
    ]
    r = put_rules(admin_client, dev, rules)
    assert r.status_code == 200, r.text
    assert {x["name"] for x in r.json()} == {"Games", "Minecraft (Java)", "TikTok"}
    state = agent.get("/api/v1/agent/state").json()
    snap = state["app_rules"]
    # App-specific rules come first, so Minecraft's own "allowed" beats the Games schedule.
    assert [x["target"] for x in snap] == ["minecraft_java", "tiktok", "games"]
    games = snap[2]
    assert games["mode"] == "schedule" and len(games["allowed"]) >= 14
    london = ZoneInfo("Europe/London")
    first = datetime.fromisoformat(games["allowed"][0]["start"]).astimezone(london)
    assert first.strftime("%H:%M") in ("16:00", "10:00")
    assert any(a["id"] == "roblox" for a in state["app_catalog"])
    assert len(state["day_starts"]) >= 15
    today = state["day_starts"][1]
    assert today["day"] == datetime.now(london).date().isoformat()
    assert datetime.fromisoformat(today["start"]).astimezone(london).strftime("%H:%M") == "00:00"
    assert admin_client.get(RULES.format(dev)).json()[0]["target"] in {"minecraft_java", "tiktok"}


def test_rules_are_validated(admin_client, agent):
    dev = agent.device_id
    assert put_rules(admin_client, dev, [{"target_kind": "app", "target": "nope", "mode": "block"}]).status_code == 422
    assert put_rules(admin_client, dev, [{"target_kind": "category", "target": "games", "mode": "schedule", "windows": []}]).status_code == 422
    assert put_rules(admin_client, dev, [{"target_kind": "category", "target": "games", "mode": "schedule",
                                          "windows": [{"day": 0, "start": "16:00", "end": "16:00"}]}]).status_code == 422
    twice = [{"target_kind": "app", "target": "roblox", "mode": "block"}] * 2
    assert put_rules(admin_client, dev, twice).status_code == 422
    # Windows are dropped for rules that aren't schedules.
    r = put_rules(admin_client, dev, [{"target_kind": "app", "target": "roblox", "mode": "block", "windows": [{"day": 0, "start": "16:00", "end": "17:00"}]}])
    assert r.json()[0]["windows"] == []
    assert admin_client.get("/api/v1/events", params={"event_type": "config.app_rules_changed"}).json()


def test_custom_apps_can_be_ruled_and_removed(admin_client, agent):
    r = admin_client.post("/api/v1/apps/custom", json={"name": "Homework Helper", "category": "Homework apps", "exe": "helper.exe"})
    assert r.status_code == 201, r.text
    app_id = r.json()["id"]
    assert app_id.startswith("custom:")
    cat = admin_client.get("/api/v1/apps/catalog").json()
    assert cat["categories"]["Homework apps"] == "Homework apps"
    assert put_rules(admin_client, agent.device_id, [
        {"target_kind": "app", "target": app_id, "mode": "allow"},
        {"target_kind": "category", "target": "Homework apps", "mode": "allow"},
    ]).status_code == 200
    state = agent.get("/api/v1/agent/state").json()
    assert {"id": app_id, "name": "Homework Helper", "category": "Homework apps", "matchers": [{"exe": "helper.exe"}]} in state["app_catalog"]
    assert admin_client.delete(f"/api/v1/apps/custom/{app_id.split(':')[1]}").status_code == 204
    assert [x["target"] for x in admin_client.get(RULES.format(agent.device_id)).json()] == ["Homework apps"]

    assert admin_client.post("/api/v1/apps/custom", json={"name": "X", "category": "Games"}).status_code == 422
    assert admin_client.post("/api/v1/apps/custom", json={"name": "X", "category": "Games", "exe": "C:\\x\\y.exe"}).status_code == 422


def test_usage_reports_become_daily_totals(admin_client, agent):
    today = datetime.now(ZoneInfo("Europe/London")).date()
    yesterday = today - timedelta(days=1)
    report = {"days": {
        today.isoformat(): {
            "_total": {"name": "Screen time", "seconds": 5400},
            "minecraft_java": {"name": "Minecraft (Java)", "category": "games", "seconds": 3600, "path": "C:\\Java\\javaw.exe"},
            "exe:notes.exe": {"name": "Notes", "seconds": 600, "path": "C:\\Apps\\notes.exe"},
        },
        yesterday.isoformat(): {"chrome": {"name": "Google Chrome", "category": "browsers", "seconds": 1200}},
        "2001-01-01": {"chrome": {"name": "old", "seconds": 5}},
        "nonsense": {"x": {"seconds": 1}},
    }}
    assert agent.post("/api/v1/agent/reports/usage", json=report).status_code == 204
    # Later totals replace earlier ones (the agent sends running totals).
    report["days"][today.isoformat()]["minecraft_java"]["seconds"] = 4000
    agent.post("/api/v1/agent/reports/usage", json=report)
    days = admin_client.get(f"/api/v1/devices/{agent.device_id}/usage").json()
    assert [d["day"] for d in days] == [today.isoformat(), yesterday.isoformat()]
    assert days[0]["total_seconds"] == 5400
    assert [(a["key"], a["seconds"]) for a in days[0]["apps"]] == [("minecraft_java", 4000), ("exe:notes.exe", 600)]
    assert days[1]["total_seconds"] == 1200  # no total reported: the sum of the apps

    seen = {s["key"]: s for s in admin_client.get(f"/api/v1/devices/{agent.device_id}/apps/seen").json()}
    assert seen["exe:notes.exe"]["exe"] == "notes.exe" and seen["exe:notes.exe"]["catalog_id"] is None
    assert seen["minecraft_java"]["catalog_id"] == "minecraft_java"


def test_seen_apps_include_what_is_open_now(admin_client, agent):
    agent.post("/api/v1/agent/reports/processes", json={"processes": [
        {"pid": 1, "name": "Game", "path": "D:\\Games\\Game.exe", "has_window": True, "system": False, "product": "Cool Game", "company": "Indie"},
        {"pid": 2, "name": "svc", "path": "C:\\x\\svc.exe", "has_window": False, "system": False},
    ]})
    seen = admin_client.get(f"/api/v1/devices/{agent.device_id}/apps/seen").json()
    assert [(s["key"], s["product"]) for s in seen] == [("exe:game.exe", "Cool Game")]


def test_weekly_windows_cross_midnight_and_daylight_saving():
    now = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)  # the UK clocks change on Sunday 29 March
    windows, _ = expand_weekly([(5, datetime.strptime("22:00", "%H:%M").time(), datetime.strptime("07:00", "%H:%M").time())], "Europe/London", now)
    sat = windows[0]
    # Starts at 22:00 GMT, ends at 07:00 BST (06:00 UTC) after the clocks go forward.
    assert sat["start"].startswith("2026-03-28T22:00") and sat["end"].startswith("2026-03-29T06:00")
