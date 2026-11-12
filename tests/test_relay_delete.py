import json

import pytest
import requests
import responses

import main

CHANNEL = "111"
MESSAGES_URL = f"{main.DISCORD_API_BASE}/channels/{CHANNEL}/messages"


def relay_message(message_id, urls):
    items = [{"url": url, "subreddit": "pics", "postId": f"t3_{index}"} for index, url in enumerate(urls)]
    return {"id": message_id, "content": "RELAY:" + json.dumps(items), "webhook_id": "hook"}


@pytest.fixture(autouse=True)
def relay_settings(monkeypatch):
    monkeypatch.setattr(main, "RELAY_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "RELAY_CHANNEL_ID", CHANNEL)
    monkeypatch.setattr(main, "RELAY_WEBHOOK_ID", "")
    monkeypatch.setattr(main, "DELETE_RELAY_MESSAGES", True)


IMAGE_A = "https://i.redd.it/a.jpg"
IMAGE_B = "https://i.redd.it/b.png"
VIDEO = "https://v.redd.it/abc/HLSPlaylist.m3u8"


def test_messages_with_videos_are_left_for_the_video_relay():
    messages = [relay_message("1", [IMAGE_A]), relay_message("2", [IMAGE_B, VIDEO])]
    assert main.fully_used_relay_messages(messages, {IMAGE_A: True, IMAGE_B: True}) == ["1"]


def test_messages_with_failed_downloads_are_kept():
    messages = [relay_message("1", [IMAGE_A, IMAGE_B])]
    assert main.fully_used_relay_messages(messages, {IMAGE_A: True, IMAGE_B: False}) == []


def test_non_relay_messages_are_ignored():
    messages = [{"id": "9", "content": "hi", "webhook_id": "hook"}]
    assert main.fully_used_relay_messages(messages, {}) == []


@responses.activate
def test_used_messages_are_deleted():
    responses.delete(f"{MESSAGES_URL}/1", status=204)
    assert main.delete_used_relay_messages([relay_message("1", [IMAGE_A])], {IMAGE_A: True}) == 1


def test_deleting_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(main, "DELETE_RELAY_MESSAGES", False)
    assert main.delete_used_relay_messages([relay_message("1", [IMAGE_A])], {IMAGE_A: True}) == 0


@responses.activate
def test_missing_permission_stops_quietly(capsys):
    responses.delete(f"{MESSAGES_URL}/1", status=403)
    messages = [relay_message("1", [IMAGE_A]), relay_message("2", [IMAGE_B])]
    assert main.delete_used_relay_messages(messages, {IMAGE_A: True, IMAGE_B: True}) == 0
    assert len(responses.calls) == 1
    assert "Manage Messages" in capsys.readouterr().out


@responses.activate
def test_network_error_while_deleting_does_not_crash_the_run(capsys):
    responses.delete(f"{MESSAGES_URL}/1", body=requests.ConnectionError("boom"))
    assert main.delete_used_relay_messages([relay_message("1", [IMAGE_A])], {IMAGE_A: True}) == 0
    assert "boom" in capsys.readouterr().out
