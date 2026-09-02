from fastapi.testclient import TestClient

from app.main import app
from app.services import desktop_assets

INTERNAL_SECRET = "test-internal-secret"


def _desktop_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    from app.config import settings

    monkeypatch.setattr(settings, "internal_api_secret", INTERNAL_SECRET)
    monkeypatch.setattr(settings, "desktop_assets_dir", str(tmp_path / "desktop"))
    return TestClient(app)


def test_upload_and_download_desktop_assets(tmp_path, monkeypatch):
    client = _desktop_client(tmp_path, monkeypatch)
    dmg = tmp_path / "upload.dmg"
    dmg.write_bytes(b"dmg-bytes")
    setup = tmp_path / "TradeDeskyWatcher-1.4.0-setup.exe"
    setup.write_bytes(b"exe-bytes")
    appcast = tmp_path / "appcast.xml"
    appcast.write_text("<rss/>", encoding="utf-8")

    res = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        files=[
            ("files", ("TradeDeskyWatcher-1.4.0.dmg", dmg.read_bytes(), "application/octet-stream")),
            ("files", (setup.name, setup.read_bytes(), "application/octet-stream")),
            ("files", ("appcast.xml", appcast.read_bytes(), "application/xml")),
        ],
    )
    assert res.status_code == 200
    saved = res.json()["saved"]
    assert "TradeDeskyWatcher-1.4.0.dmg" in saved
    assert desktop_assets.LATEST_MAC in saved
    assert desktop_assets.LATEST_WIN_SETUP in saved

    latest_mac = client.get(f"/desktop/{desktop_assets.LATEST_MAC}")
    assert latest_mac.status_code == 200
    assert latest_mac.content == b"dmg-bytes"

    feed = client.get("/desktop/appcast.xml")
    assert feed.status_code == 200
    assert feed.content == b"<rss/>"

    missing = client.get("/desktop/nope.dmg")
    assert missing.status_code == 404

    bad = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        files=[("files", ("../escape.dmg", b"x", "application/octet-stream"))],
    )
    assert bad.status_code == 400

    unauthorized = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": "wrong"},
        files=[("files", ("ok.dmg", b"x", "application/octet-stream"))],
    )
    assert unauthorized.status_code == 401


def test_ninjatrader_receiver_latest_aliases(tmp_path, monkeypatch):
    client = _desktop_client(tmp_path, monkeypatch)
    setup = b"nt-setup-bytes"
    zip_bytes = b"nt-zip-bytes"
    versioned_appcast = b"<rss version='nt'/>"

    res = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        files=[
            (
                "files",
                (
                    "TradeDeskyNinjaTraderReceiver-2.0.0-setup.exe",
                    setup,
                    "application/octet-stream",
                ),
            ),
            (
                "files",
                (
                    "TradeDeskyNinjaTraderReceiver-2.0.0-win.zip",
                    zip_bytes,
                    "application/zip",
                ),
            ),
            (
                "files",
                (
                    "TradeDeskyNinjaTraderReceiver-2.0.0-appcast.xml",
                    versioned_appcast,
                    "application/xml",
                ),
            ),
        ],
    )
    assert res.status_code == 200
    saved = res.json()["saved"]
    assert "TradeDeskyNinjaTraderReceiver-2.0.0-setup.exe" in saved
    assert desktop_assets.LATEST_NT_WIN_SETUP in saved
    assert "TradeDeskyNinjaTraderReceiver-2.0.0-win.zip" in saved
    assert desktop_assets.LATEST_NT_WIN_ZIP in saved
    assert "TradeDeskyNinjaTraderReceiver-2.0.0-appcast.xml" in saved
    assert desktop_assets.LATEST_NT_APPCAST in saved

    setup_res = client.get(f"/desktop/{desktop_assets.LATEST_NT_WIN_SETUP}")
    assert setup_res.status_code == 200
    assert setup_res.content == setup

    zip_res = client.get(f"/desktop/{desktop_assets.LATEST_NT_WIN_ZIP}")
    assert zip_res.status_code == 200
    assert zip_res.content == zip_bytes

    appcast_res = client.get(f"/desktop/{desktop_assets.LATEST_NT_APPCAST}")
    assert appcast_res.status_code == 200
    assert appcast_res.content == versioned_appcast


def test_ninjatrader_receiver_stable_appcast_upload(tmp_path, monkeypatch):
    client = _desktop_client(tmp_path, monkeypatch)
    stable_appcast = b"<rss stable='nt'/>"

    res = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        files=[
            (
                "files",
                (
                    desktop_assets.LATEST_NT_APPCAST,
                    stable_appcast,
                    "application/xml",
                ),
            ),
        ],
    )
    assert res.status_code == 200
    saved = res.json()["saved"]
    assert saved == [desktop_assets.LATEST_NT_APPCAST]

    appcast_res = client.get(f"/desktop/{desktop_assets.LATEST_NT_APPCAST}")
    assert appcast_res.status_code == 200
    assert appcast_res.content == stable_appcast


def test_watcher_aliases_unaffected_by_ninjatrader_rules(tmp_path, monkeypatch):
    client = _desktop_client(tmp_path, monkeypatch)
    res = client.post(
        "/v1/internal/desktop/assets",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        files=[
            ("files", ("TradeDeskyWatcher-1.0.0-win.zip", b"watcher-zip", "application/zip")),
        ],
    )
    assert res.status_code == 200
    saved = res.json()["saved"]
    assert desktop_assets.LATEST_WIN_ZIP in saved
    assert desktop_assets.LATEST_NT_WIN_ZIP not in saved


def test_desktop_asset_cache_control():
    assert desktop_assets.cache_control(desktop_assets.LATEST_NT_WIN_SETUP) == "public, max-age=300"
    assert desktop_assets.cache_control(desktop_assets.LATEST_NT_WIN_ZIP) == "public, max-age=300"
    assert desktop_assets.cache_control(desktop_assets.LATEST_NT_APPCAST) == "public, max-age=300"
    assert desktop_assets.cache_control("appcast.xml") == "public, max-age=300"
    assert desktop_assets.cache_control(desktop_assets.LATEST_MAC) == "public, max-age=300"

    assert (
        desktop_assets.cache_control("TradeDeskyNinjaTraderReceiver-2.0.0-setup.exe")
        == "public, max-age=31536000, immutable"
    )
    assert (
        desktop_assets.cache_control("TradeDeskyNinjaTraderReceiver-2.0.0-win.zip")
        == "public, max-age=31536000, immutable"
    )
    assert (
        desktop_assets.cache_control("TradeDeskyNinjaTraderReceiver-2.0.0-appcast.xml")
        == "public, max-age=31536000, immutable"
    )
