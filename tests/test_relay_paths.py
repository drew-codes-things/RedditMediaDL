import json
import os

import pytest

import main


def relay_message(post_id):
    items = [{"url": "https://i.redd.it/a.jpg", "subreddit": "pics", "postId": post_id}]
    return {"id": "1", "content": "RELAY:" + json.dumps(items), "webhook_id": "hook"}


@pytest.fixture
def relay_downloads(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(main, "RELAY_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "RELAY_CHANNEL_ID", "111")
    monkeypatch.setattr(main, "RELAY_WEBHOOK_ID", "")
    monkeypatch.setattr(main, "DELETE_RELAY_MESSAGES", False)
    monkeypatch.setattr(main, "get_downloads_folder", lambda content_type: str(tmp_path / "Media"))
    monkeypatch.setattr(main, "get_relay_state_path", lambda: str(tmp_path / "relay_last_message_id.txt"))
    monkeypatch.setattr(main, "download_file", lambda url, filename, folder: saved.append((filename, folder)) or True)
    return saved


@pytest.mark.parametrize("post_id", ["../../outside", "t3_../../outside", "/etc/cron.d/job", "..\\..\\outside"])
def test_relay_post_id_cannot_leave_the_downloads_folder(relay_downloads, monkeypatch, post_id):
    monkeypatch.setattr(main, "fetch_relay_messages", lambda after_id: [relay_message(post_id)])
    main.download_from_relay(1)
    (filename, folder), = relay_downloads
    assert os.sep not in filename and "/" not in filename and "\\" not in filename
    assert os.path.dirname(os.path.normpath(os.path.join(folder, filename))) == folder


def test_normal_relay_post_id_keeps_its_name(relay_downloads, monkeypatch):
    monkeypatch.setattr(main, "fetch_relay_messages", lambda after_id: [relay_message("t3_1abc9z")])
    main.download_from_relay(1)
    assert [filename for filename, _ in relay_downloads] == ["1abc9z_001.jpg"]
