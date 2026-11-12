import json

import pytest

import main

POST = {"id": "abc123", "title": "a post", "selftext": "body text"}


@pytest.fixture
def scrape_env(monkeypatch, tmp_path):
    resolve_calls = []
    downloads = []

    def fake_resolve(post, resolve=False):
        resolve_calls.append(resolve)
        return ["https://i.redd.it/found.jpg"] if resolve else []

    monkeypatch.setattr(main, "fetch_posts", lambda name, count, sort="hot": [dict(POST)])
    monkeypatch.setattr(main, "resolve_post_media_urls", fake_resolve)
    monkeypatch.setattr(main, "get_seen_path", lambda: str(tmp_path / "seen_ids.json"))
    monkeypatch.setattr(main, "get_downloads_folder", lambda content_type: str(tmp_path / content_type))
    monkeypatch.setattr(main, "download_file", lambda url, name, folder: downloads.append(("media", name)) or True)
    monkeypatch.setattr(main, "save_text", lambda post, name, folder: downloads.append(("text", name)) or True)
    return resolve_calls, downloads


def test_post_without_images_is_looked_at_again_with_resolve(scrape_env):
    resolve_calls, downloads = scrape_env
    main.scrape_reddit("pics", 1, 1, "media", resolve=False)
    main.scrape_reddit("pics", 1, 1, "media", resolve=True)
    assert resolve_calls == [False, True]
    assert downloads == [("media", "abc123.jpg")]


def test_media_run_does_not_hide_a_post_from_a_later_text_run(scrape_env):
    _, downloads = scrape_env
    main.scrape_reddit("pics", 1, 1, "media", resolve=False)
    main.scrape_reddit("pics", 1, 1, "text", resolve=False)
    assert downloads == [("text", "abc123_text.txt")]


def test_finished_post_is_skipped_on_the_next_run_in_the_same_mode(scrape_env):
    resolve_calls, downloads = scrape_env
    main.scrape_reddit("pics", 1, 1, "media", resolve=True)
    main.scrape_reddit("pics", 1, 1, "media", resolve=True)
    assert downloads == [("media", "abc123.jpg")]
    assert resolve_calls == [True]


def test_post_without_images_is_skipped_on_the_next_run_in_the_same_mode(scrape_env, tmp_path):
    resolve_calls, _ = scrape_env
    main.scrape_reddit("pics", 1, 1, "media", resolve=False)
    main.scrape_reddit("pics", 1, 1, "media", resolve=False)
    assert resolve_calls == [False]
    assert json.loads((tmp_path / "seen_ids.json").read_text())
