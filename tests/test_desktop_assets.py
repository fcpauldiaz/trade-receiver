from fastapi.testclient import TestClient

from app.main import app
from app.services import desktop_assets

INTERNAL_SECRET = "test-internal-secret"


def test_upload_and_download_desktop_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    from app.config import settings

    monkeypatch.setattr(settings, "internal_api_secret", INTERNAL_SECRET)
    monkeypatch.setattr(settings, "desktop_assets_dir", str(tmp_path / "desktop"))

    client = TestClient(app)
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
