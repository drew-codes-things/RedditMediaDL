import json

import pytest
import responses

import main

GONE_URL = "https://i.redd.it/removed.jpg"
GOOD_URL = "https://i.redd.it/kept.jpg"


def relay_message(message_id, urls):
    items = [{"url": url, "subreddit": "pics", "postId": "t3_abc"} for url in urls]
    return {"id": message_id, "content": "RELAY:" + json.dumps(items), "webhook_id": "hook"}


@pytest.fixture
def relay_env(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RELAY_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "RELAY_CHANNEL_ID", "111")
    monkeypatch.setattr(main, "RELAY_WEBHOOK_ID", "")
    monkeypatch.setattr(main, "DELETE_RELAY_MESSAGES", False)
    monkeypatch.setattr(main, "get_downloads_folder", lambda content_type: str(tmp_path))
    state = tmp_path / "relay_last_message_id.txt"
    monkeypatch.setattr(main, "get_relay_state_path", lambda: str(state))
    return state


@responses.activate
@pytest.mark.parametrize("status", [404, 410])
def test_image_that_no_longer_exists_does_not_hold_the_cursor(relay_env, monkeypatch, status):
    responses.get(GONE_URL, status=status)
    responses.get(GOOD_URL, body=b"img")
    monkeypatch.setattr(main, "fetch_relay_messages", lambda after_id: [relay_message("7", [GONE_URL, GOOD_URL])])
    main.download_from_relay(1)
    assert relay_env.read_text() == "7"


@responses.activate
def test_server_error_still_holds_the_cursor_for_a_retry(relay_env, monkeypatch):
    monkeypatch.setattr(main, "RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    responses.get(GONE_URL, status=403)
    monkeypatch.setattr(main, "fetch_relay_messages", lambda after_id: [relay_message("7", [GONE_URL])])
    main.download_from_relay(1)
    assert not relay_env.exists()
