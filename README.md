<div align="center">

# RedditDownloaderV3

**A Python Reddit image and text downloader that works entirely from RSS feeds. No API key required.**

[![Python](https://img.shields.io/badge/python-3.8+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## Overview

RedditDownloaderV3 fetches posts from any subreddit via Reddit's public RSS/Atom feed, resolves image URLs, and downloads them in parallel using a `ThreadPoolExecutor`. No Reddit API credentials are required at any point. Only original image formats are downloaded: `.jpg`, `.jpeg`, `.png`, `.webp`. Videos, GIFs, and `.webm` files are intentionally excluded.

Posts are fetched by sort order (hot, new, top, rising). Media is saved to `Reddit_downloads/Media/` and text posts to `Reddit_downloads/Text/`, both relative to the script directory.

---

## How It Works

```
User input  ->  subreddit RSS fetch  ->  URL extraction  ->  resolve  ->  parallel download
```

1. The tool normalises your input (name, `r/name`, full URL) to a bare subreddit name
2. It fetches the RSS/Atom feed and parses posts using Python's `xml.etree.ElementTree`
3. Image URLs are extracted from the HTML content of each feed entry
4. Optionally, post pages are scraped to resolve `i.redd.it`, `preview.redd.it`, and Imgur URLs
5. Files are downloaded in parallel with exponential backoff and already-exist skip logic

---

## URL Resolution

| Source | Behaviour |
|---|---|
| Direct image URL (any host) | Downloaded as-is if extension matches |
| `i.imgur.com` direct | Downloaded as-is |
| Imgur album / gallery (`/a/` or `/gallery/`) | Page scraped for all `i.imgur.com` image URLs |
| Imgur single page | Tried with `.jpg` and `.png` extensions; falls back to page scrape |
| `i.redd.it` | Post page scraped for original image URL |
| `preview.redd.it` | Query string stripped, kept if extension matches |
| Reddit post URL | Post page scraped for `i.redd.it` images |

URL resolution from post pages is disabled by default to keep requests minimal. Pass `--resolve` to enable it.

---

## Usage

```bash
python main.py
```

To enable post-page scraping for deeper image resolution:

```bash
python main.py --resolve
```

Resume is on by default: each subreddit's processed post IDs are saved to `Reddit_downloads/seen_ids.json`, so re-runs skip posts already handled even if their files were moved or deleted. To ignore the saved list and reprocess everything:

```bash
python main.py --no-resume
```

The tool prompts for:

1. Subreddit name (accepts `cats`, `r/cats`, or a full Reddit URL)
2. Download type: `M` (media), `T` (text), or `B` (both)
3. Sort order: `1` hot, `2` new, `3` top, `4` rising
4. Number of posts to fetch
5. Number of concurrent download threads (capped at 10)

---

## Output Structure

```
Reddit_downloads/
  Media/
    <post_id>.jpg
    <post_id>_001.png
    <post_id>_002.png
  Text/
    <post_id>_text.txt
```

Text files contain title, author, score, permalink URL, and the post body.

For posts with multiple images, files are named `<post_id>_001.ext`, `<post_id>_002.ext`, etc. Single-image posts use `<post_id>.ext` with no suffix.

---

## Retry Behaviour

- Up to 5 retry attempts per request
- Exponential backoff: base 3s, doubling each attempt (3s, 6s, 12s, 24s)
- Backoff includes +-20% random jitter
- `Retry-After` header respected on 429 responses
- 5xx server errors also trigger retry with backoff
- RSS requests use a 45s timeout; download requests use a 60s timeout

---

## Notes

- Reddit blocks API access without credentials, but public RSS feeds remain accessible. This tool uses only those feeds.
- Reddit sometimes returns 403 on subreddit validation requests. If blocked, the tool falls back to using the candidate name directly.
- The subreddit search tries an exact name match and a simple plural/singular variant (e.g. `cat` -> `cats`).
- Already-downloaded files are skipped by filename; no hash checking is performed.
- The User-Agent is set to `linux:reddit-image-downloader:v1.0 (by /u/drew-gnr)`.

---

---

## Install as a command (pipx)

Install this folder as a CLI so it is available on your PATH:

```bash
pipx install .
reddit-downloader
```

Logging: set `LOG_LEVEL` (e.g. `DEBUG`) and `LOG_FILE` to also write logs to a file.


## License

MIT - made by [Drew](https://github.com/drew-codes-things)
