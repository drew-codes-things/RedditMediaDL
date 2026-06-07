import html
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

import requests
from tqdm import tqdm

import logging

_log_handlers = [logging.StreamHandler()]
_log_file = os.environ.get("LOG_FILE")
if _log_file:
    _log_handlers.append(logging.FileHandler(_log_file))
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(levelname)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("redditdownloader")


HEADERS = {
    "User-Agent": "linux:reddit-image-downloader:v1.0 (by /u/drew-gnr)",
    "Accept": "*/*",
}
MAX_THREADS = 10
RETRY_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 3.0
RSS_TIMEOUT = 45
SCRAPE_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
REDDIT_BASE = "https://www.reddit.com"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def is_image_url(url):
    path = urlparse(url).path.lower().split("?")[0]
    return path.endswith(IMAGE_EXTS)



def _backoff_wait(attempt, base=RETRY_BACKOFF_SECONDS):
    """Exponential backoff with +-20% jitter."""
    wait = base * (2 ** attempt)
    jitter = wait * 0.2 * (random.random() * 2 - 1)
    return max(0.5, wait + jitter)


def request_with_retry(url, *, stream=False, timeout=SCRAPE_TIMEOUT, headers=None, retries=RETRY_ATTEMPTS):
    headers = headers or HEADERS
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, stream=stream, headers=headers, timeout=timeout)
            if resp.status_code == 429 and attempt < retries - 1:
                try:
                    retry_after = int(resp.headers.get("Retry-After", 0) or 0)
                except (TypeError, ValueError):
                    retry_after = 0
                wait = retry_after if retry_after > 0 else _backoff_wait(attempt)
                print(f"[RETRY] Rate limited -> waiting {wait:.1f}s ({attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600 and attempt < retries - 1:
                wait = _backoff_wait(attempt)
                print(f"[RETRY] HTTP {resp.status_code} -> waiting {wait:.1f}s ({attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt == retries - 1:
                raise
            wait = _backoff_wait(attempt)
            print(f"[RETRY] Timed out ({timeout}s) -> waiting {wait:.1f}s ({attempt + 1}/{retries})")
            time.sleep(wait)
        except requests.RequestException as e:
            last_error = e
            if attempt == retries - 1:
                raise
            wait = _backoff_wait(attempt)
            print(f"[RETRY] Network error: {e} -> waiting {wait:.1f}s ({attempt + 1}/{retries})")
            time.sleep(wait)
    if last_error:
        raise last_error
    raise requests.RequestException(f"Request failed for {url}")


def fetch_html(url, timeout=SCRAPE_TIMEOUT):
    try:
        resp = request_with_retry(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return ""



def normalize_subreddit_name(value):
    def _clean_candidate(name):
        if not name:
            return ""
        cleaned = name.strip().strip("/")
        cleaned = re.sub(r"\.(?:json|rss)$", "", cleaned, flags=re.I)
        cleaned = cleaned.split("?")[0].split("#")[0]
        return cleaned.split("/")[0].strip()

    if not value:
        return ""
    raw = value.strip()
    if not raw:
        return ""
    if raw.lower().startswith(("reddit.com/", "www.reddit.com/", "old.reddit.com/", "m.reddit.com/")):
        raw = "https://" + raw
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        path = parsed.path.strip("/")
        match = re.search(r"(?:^|/)r/([^/]+)", path, flags=re.I)
        if match:
            return _clean_candidate(match.group(1))
        return _clean_candidate(path)
    match = re.search(r"(?:^|/)r/([^/]+)", raw, flags=re.I)
    if match:
        return _clean_candidate(match.group(1))
    if raw.lower().startswith("r/"):
        return _clean_candidate(raw[2:])
    return _clean_candidate(raw)


def reddit_rss_get(path, *, query=None, timeout=RSS_TIMEOUT):
    query = query or ""
    url = f"{REDDIT_BASE}{path}"
    if query:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    resp = request_with_retry(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 403:
        raise requests.RequestException(f"403 Blocked from {url}")
    resp.raise_for_status()
    return resp.text


def first_working_rss(paths, *, query=None, timeout=RSS_TIMEOUT):
    last_error = None
    for path in paths:
        try:
            return reddit_rss_get(path, query=query, timeout=timeout)
        except requests.RequestException as e:
            last_error = e
    if last_error:
        raise last_error
    raise requests.RequestException("No Reddit RSS paths were available")


def subreddit_listing_paths(subreddit_name, sort):
    encoded = quote(subreddit_name)
    if sort == "hot":
        return [f"/r/{encoded}.rss", f"/r/{encoded}/.rss"]
    return [f"/r/{encoded}/{sort}.rss", f"/r/{encoded}/{sort}/.rss"]



def get_downloads_folder(content_type):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(script_dir, "Reddit_downloads", content_type.capitalize())
    os.makedirs(folder, exist_ok=True)
    return folder


def get_seen_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(script_dir, "Reddit_downloads")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "seen_ids.json")


def load_seen(subreddit_name):
    """Return the set of post IDs already processed for this subreddit."""
    try:
        with open(get_seen_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get(subreddit_name.lower(), []))
    except (FileNotFoundError, ValueError, AttributeError):
        return set()


def save_seen(subreddit_name, ids):
    path = get_seen_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, ValueError):
        data = {}
    data[subreddit_name.lower()] = sorted(ids)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def normalize_media_url(url):
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    return url


def _url_key(url):
    return url.split("?")[0].rstrip("/")


def dedup_urls(urls):
    seen = set()
    result = []
    for u in urls:
        k = _url_key(u)
        if k not in seen:
            seen.add(k)
            result.append(u)
    return result



def resolve_imgur_url(url):
    parsed = urlparse(url)
    path = parsed.path.lower()

    if is_image_url(url) and "imgur.com" in parsed.netloc:
        return [url]

    if "/a/" in path or "/gallery/" in path:
        page_html = fetch_html(url)
        if not page_html:
            return []
        found = re.findall(
            r'https?://i\.imgur\.com/[A-Za-z0-9]+(?:\.(?:jpg|jpeg|png|webp))',
            page_html
        )
        return dedup_urls(found)

    img_id = path.strip("/").split("/")[-1]
    if img_id:
        for ext in (".jpg", ".png"):
            candidate = f"https://i.imgur.com/{img_id}{ext}"
            try:
                r = request_with_retry(candidate, timeout=10)
                if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                    return [candidate]
            except Exception:
                continue
        page_html = fetch_html(url)
        found = re.findall(
            r'https?://i\.imgur\.com/[A-Za-z0-9]+(?:\.(?:jpg|jpeg|png|webp))',
            page_html
        )
        return dedup_urls(found)

    return []


def resolve_ireddit_url(post_link):
    """Resolve i.redd.it original images from a post page."""
    page_html = fetch_html(post_link, timeout=SCRAPE_TIMEOUT)
    if not page_html:
        return []
    found = []
    found += re.findall(
        r'https?://i\.redd\.it/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)',
        page_html
    )
    previews = re.findall(r'https?://preview\.redd\.it/[^\"\' ]+', page_html)
    for p in previews:
        p = html.unescape(p).split("?")[0]
        if is_image_url(p):
            found.append(p)
    return dedup_urls(found)


def resolve_url(url, post_link, resolve=True):
    if not url:
        return []
    url = normalize_media_url(url)
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    clean_path = url.split("?")[0]

    if is_image_url(clean_path):
        return [clean_path]

    if not resolve:
        return []

    if "imgur.com" in host:
        return resolve_imgur_url(url)
    if "i.redd.it" in host:
        return resolve_ireddit_url(post_link)
    if "reddit.com" in host and "/comments/" in url:
        return resolve_ireddit_url(url)

    return []



def extract_media_urls_from_html(content):
    content = content or ""
    candidates = []
    candidates += re.findall(r'(?:href|src)=["\']([^"\']+)["\']', content, flags=re.I)
    candidates += re.findall(r'https?://[^\s<>"\'`]+', content)
    seen = set()
    urls = []
    for raw in candidates:
        url = normalize_media_url(raw).rstrip("),.;")
        if not url.startswith(("http://", "https://")):
            continue
        k = _url_key(url)
        if k in seen:
            continue
        seen.add(k)
        urls.append(url)
    return urls


def html_to_text(content):
    text = re.sub(r"<br\s*/?>", "\n", content or "", flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def atom_text(node, name, default=""):
    child = node.find(f"atom:{name}", ATOM_NS)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def atom_link(entry):
    links = entry.findall("atom:link", ATOM_NS)
    if not links:
        return ""
    for link in links:
        href = link.get("href")
        rel = link.get("rel", "alternate")
        if href and rel == "alternate":
            return href
    return links[0].get("href", "")


def post_id_from_link(link, fallback):
    match = re.search(r"/comments/([^/]+)/", link or "")
    if match:
        return match.group(1)
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", fallback or "post").strip("_")
    return cleaned[:48] or "post"


def parse_reddit_atom_feed(feed_text, subreddit_name, limit):
    root = ET.fromstring(feed_text)
    entries = root.findall("atom:entry", ATOM_NS)
    posts = []
    for entry in entries[:limit]:
        title = html.unescape(atom_text(entry, "title", "Untitled"))
        content_html = atom_text(entry, "content", "")
        link = atom_link(entry)
        parsed_link = urlparse(link)
        permalink = parsed_link.path or ""
        post_id = post_id_from_link(link, atom_text(entry, "id", title))
        author_node = entry.find("atom:author/atom:name", ATOM_NS)
        author = author_node.text.strip() if author_node is not None and author_node.text else "[unknown]"
        rss_candidates = extract_media_urls_from_html(content_html)
        posts.append({
            "id": post_id,
            "title": title,
            "author": author,
            "score": 0,
            "permalink": permalink,
            "post_link": link,
            "selftext": html_to_text(content_html),
            "rss_candidates": rss_candidates,
        })
    return posts


def parse_reddit_rss_feed(feed_text, subreddit_name, limit):
    root = ET.fromstring(feed_text)
    items = root.findall("./channel/item")
    posts = []
    for item in items[:limit]:
        title_node = item.find("title")
        link_node = item.find("link")
        desc_node = item.find("description")
        title = html.unescape(title_node.text.strip()) if title_node is not None and title_node.text else "Untitled"
        link = link_node.text.strip() if link_node is not None and link_node.text else ""
        content_html = desc_node.text if desc_node is not None and desc_node.text else ""
        parsed_link = urlparse(link)
        permalink = parsed_link.path or ""
        post_id = post_id_from_link(link, title)
        rss_candidates = extract_media_urls_from_html(content_html)
        posts.append({
            "id": post_id,
            "title": title,
            "author": "[unknown]",
            "score": 0,
            "permalink": permalink,
            "post_link": link,
            "selftext": html_to_text(content_html),
            "rss_candidates": rss_candidates,
        })
    return posts


def parse_reddit_feed(feed_text, subreddit_name, limit):
    root = ET.fromstring(feed_text)
    if root.tag.endswith("feed"):
        return parse_reddit_atom_feed(feed_text, subreddit_name, limit)
    return parse_reddit_rss_feed(feed_text, subreddit_name, limit)



def resolve_post_media_urls(post, resolve=False):
    post_link = post.get("post_link", "")
    candidates = post.get("rss_candidates", [])

    skip_patterns = (
        "redditmedia.com/",
        "redditstatic.com/",
        "styles.redditmedia.com/",
        "thumbs.redditmedia.com/",
        "/icon",
        "/logo",
        "/snoo",
    )

    direct_urls = []
    seen = set()

    for candidate in candidates:
        if any(p in candidate for p in skip_patterns):
            continue
        resolved = resolve_url(candidate, post_link, resolve=resolve)
        for u in resolved:
            if not is_image_url(u.split("?")[0]):
                continue
            k = _url_key(u)
            if k not in seen:
                seen.add(k)
                direct_urls.append(u)

    if not direct_urls and resolve and post_link and "reddit.com" in post_link:
        scraped = resolve_ireddit_url(post_link)
        for u in scraped:
            if not is_image_url(u.split("?")[0]):
                continue
            k = _url_key(u)
            if k not in seen:
                seen.add(k)
                direct_urls.append(u)

    return direct_urls



def fetch_posts(subreddit_name, limit, sort="hot"):
    valid_sorts = ("hot", "new", "top", "rising")
    if sort not in valid_sorts:
        sort = "hot"
    subreddit_name = normalize_subreddit_name(subreddit_name)
    try:
        query = f"limit={min(100, max(limit, 1))}"
        feed_text = first_working_rss(
            subreddit_listing_paths(subreddit_name, sort),
            query=query,
            timeout=RSS_TIMEOUT
        )
        posts = parse_reddit_feed(feed_text, subreddit_name, limit)
    except (requests.RequestException, ET.ParseError) as e:
        logger.error(f"Error fetching posts from Reddit RSS: {e}")
        return []
    return posts[:limit]



def build_subreddit_candidates(query):
    """Build a small list of candidate subreddit names from the user's query.
    Tries the exact name first, then a simple plural/singular variant.
    No topic-specific guessing - works for any subreddit.
    """
    base = normalize_subreddit_name(query).lower()
    if not base:
        return []

    candidates = [base]

    if base.endswith("s") and len(base) > 2:
        candidates.append(base[:-1])
    else:
        candidates.append(f"{base}s")

    unique = []
    seen = set()
    for c in candidates:
        cleaned = normalize_subreddit_name(c).lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def fallback_subreddit_result(subreddit_name):
    return {"display_name": subreddit_name, "title": "", "public_description": "", "subscribers": 0}


def subreddit_result_from_feed(subreddit_name, feed_text):
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return fallback_subreddit_result(subreddit_name)
    title = ""
    if root.tag.endswith("feed"):
        title = atom_text(root, "title", "")
        entries = root.findall("atom:entry", ATOM_NS)
    else:
        title_node = root.find("./channel/title")
        title = title_node.text.strip() if title_node is not None and title_node.text else ""
        entries = root.findall("./channel/item")
    if not entries:
        return fallback_subreddit_result(subreddit_name)
    return {"display_name": subreddit_name, "title": html.unescape(title), "public_description": "", "subscribers": 0}


def search_subreddits(query):
    q = (query or "").strip().lower()
    if not q:
        return []
    candidates = build_subreddit_candidates(query)
    matches = {}
    blocked_validation = False
    for candidate in candidates:
        try:
            feed_text = first_working_rss(
                [f"/r/{quote(candidate)}.rss", f"/r/{quote(candidate)}/.rss"],
                query="limit=1",
                timeout=RSS_TIMEOUT
            )
            result = subreddit_result_from_feed(candidate, feed_text)
        except requests.RequestException as e:
            blocked_validation = blocked_validation or "403" in str(e)
            result = None
        if result:
            matches[result["display_name"].lower()] = result
    if not matches:
        if blocked_validation:
            print("[WARN] Reddit blocked subreddit validation, showing direct candidates instead.")
        for candidate in candidates[:10]:
            matches[candidate] = fallback_subreddit_result(candidate)

    def rank_score(sub):
        name = (sub.get("display_name") or "").lower()
        if name == q:
            return (0, name)
        elif name.startswith(q):
            return (1, name)
        elif q in name:
            return (2, name)
        return (3, name)

    return sorted(matches.values(), key=rank_score)[:10]



def download_file(url, filename, download_folder):
    file_path = os.path.join(download_folder, filename)
    if os.path.exists(file_path):
        print(f"[SKIP] Already exists: {filename}")
        return "skip"

    try:
        with request_with_retry(url, stream=True, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        print(f"[OK]   Downloaded: {filename}")
        return True
    except requests.RequestException as e:
        print(f"[FAIL] {filename}: {e}")
        return False


def save_text(post, filename, download_folder):
    file_path = os.path.join(download_folder, filename)
    if os.path.exists(file_path):
        print(f"[SKIP] Already exists: {filename}")
        return "skip"
    try:
        content = (
            f"Title:   {post.get('title', '')}\n"
            f"Author:  u/{post.get('author', '[deleted]')}\n"
            f"Score:   {post.get('score', 0)}\n"
            f"URL:     https://reddit.com{post.get('permalink', '')}\n"
            f"\n{post.get('selftext', '')}"
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK]   Saved text: {filename}")
        return True
    except IOError as e:
        print(f"[FAIL] {filename}: {e}")
        return False



def scrape_reddit(subreddit_name, count, num_threads, download_type, sort="hot", resolve=False, use_resume=True):
    print(f"\nFetching up to {count} posts from r/{subreddit_name} RSS (sort: {sort})...")
    posts = fetch_posts(subreddit_name, count, sort)
    print(f"Retrieved {len(posts)} posts.")

    seen = load_seen(subreddit_name) if use_resume else set()
    new_seen = set(seen)
    if use_resume and seen:
        print(f"[INFO] Resume on: {len(seen)} previously-seen post(s) will be skipped.")

    total_downloads = 0
    total_skipped = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        # Track which post each future belongs to, so a failed download does not
        # get the post marked "seen" -- it should be retried on the next run.
        future_post = {}
        attempted_posts = set()
        failed_posts = set()
        no_media_posts = set()

        for post in posts:
            post_id = post.get("id")
            if not post_id:
                print("[SKIP] Post missing id; skipping.")
                continue

            if use_resume and post_id in seen:
                print(f"[SKIP] Already seen: {post_id}")
                total_skipped += 1
                continue

            queued_any = False

            if download_type in ["media", "both"]:
                media_urls = resolve_post_media_urls(post, resolve=resolve)
                if media_urls:
                    folder = get_downloads_folder("Media")
                    for idx, murl in enumerate(media_urls, 1):
                        ext = os.path.splitext(urlparse(murl).path)[1] or ".jpg"
                        suffix = f"_{idx:03d}" if len(media_urls) > 1 else ""
                        fname = f"{post_id}{suffix}{ext}"
                        fut = executor.submit(download_file, murl, fname, folder)
                        futures.append(fut)
                        future_post[fut] = post_id
                        queued_any = True
                else:
                    print(f"[SKIP] No images found for post {post_id} ({post.get('title', '')[:60]})")

            if download_type in ["text", "both"] and post.get("selftext", "").strip():
                filename = f"{post_id}_text.txt"
                folder = get_downloads_folder("Text")
                fut = executor.submit(save_text, post, filename, folder)
                futures.append(fut)
                future_post[fut] = post_id
                queued_any = True

            if queued_any:
                attempted_posts.add(post_id)
            else:
                # Nothing to download for this post (e.g. text/link post in media
                # mode) -- mark it seen so it isn't re-checked on every run, but
                # it never carries the risk of "succeeded" since nothing ran.
                no_media_posts.add(post_id)

        if not futures:
            print("No downloadable images/text found in the retrieved RSS posts.")
        else:
            with tqdm(total=len(futures), desc="Processing", unit="file") as pbar:
                for future in as_completed(futures):
                    post_id = future_post.get(future)
                    try:
                        result = future.result()
                        if result == "skip":
                            total_skipped += 1
                        elif result:
                            total_downloads += 1
                        else:
                            total_failed += 1
                            if post_id:
                                failed_posts.add(post_id)
                    except Exception as e:
                        print(f"Task error: {e}")
                        total_failed += 1
                        if post_id:
                            failed_posts.add(post_id)
                    pbar.update(1)

        new_seen |= no_media_posts
        new_seen |= (attempted_posts - failed_posts)

    if use_resume:
        save_seen(subreddit_name, new_seen)

    print(f"\nDone. {total_downloads} downloaded, {total_skipped} skipped (already existed/seen), {total_failed} failed.")



def print_title():
    title = r"""
 ____           _     _ _ _     ____                      _                 _           
|  _ \ ___   __| | __| (_) |_  |  _ \  _____      ___ __ | | ___   __ _  __| | ___ _ __ 
| |_) / _ \ / _` |/ _` | | __| | | | |/ _ \ \ /\ / / '_ \| |/ _ \ / _` |/ _` |/ _ \ '__|
|  _ < (_) | (_| | (_| | | |_  | |_| | (_) \ V  V /| | | | | (_) | (_| | (_| |  __/ |   
|_| \_\___/ \__,_|\__,_|_|\__| |____/ \___/ \_/\_/ |_| |_|_|\___/ \__,_|\__,_|\___|_|   

                        - made by Drew  (v2 - no API key needed)
    """
    print(title)


def main():
    resolve = "--resolve" in sys.argv
    use_resume = "--no-resume" not in sys.argv

    print_title()
    print("[INFO] Images only mode: only .jpg / .jpeg / .png / .webp will be downloaded.")
    if resolve:
        print("[INFO] --resolve mode: will scrape post pages to find original images.")
    else:
        print("[INFO] --no-resolve mode: only direct image URLs from RSS will be downloaded.")
    if not use_resume:
        print("[INFO] --no-resume mode: previously-seen post IDs will not be skipped.")

    search_query = input("Enter subreddit name (e.g. earthporn, cats, r/aww): ").strip()
    results = search_subreddits(search_query)

    if not results:
        print("No matching subreddits found. Try the exact name, e.g. 'earthporn' or 'r/aww'.")
        return

    print("\nTop results:")
    for i, sub in enumerate(results, 1):
        label = f"r/{sub['display_name']}"
        title = sub.get("title", "")
        suffix = f" - {title}" if title and title.lower() != label.lower() else ""
        print(f"  {i}. {label}{suffix}")

    try:
        choice = int(input("\nPick a number: "))
        if not (1 <= choice <= len(results)):
            print("Invalid choice.")
            return
    except ValueError:
        print("Please enter a number.")
        return

    subreddit_name = results[choice - 1]["display_name"]

    download_type = input("Download [M]edia, [T]ext or [B]oth? ").strip().lower()
    if download_type not in ["m", "t", "b"]:
        print("Invalid choice.")
        return
    download_type = {"m": "media", "t": "text", "b": "both"}[download_type]

    print("\nSort order:")
    print("  1. hot (default)")
    print("  2. new")
    print("  3. top")
    print("  4. rising")
    sort_map = {"1": "hot", "2": "new", "3": "top", "4": "rising"}
    sort_choice = input("Choose sort (1-4) [1]: ").strip() or "1"
    sort = sort_map.get(sort_choice, "hot")

    try:
        count = int(input("How many posts? "))
        raw_threads = int(input(f"Concurrent downloads (max {MAX_THREADS}): "))
        if count <= 0 or raw_threads <= 0:
            raise ValueError
    except ValueError:
        print("Please enter positive integers.")
        return

    if raw_threads > MAX_THREADS:
        print(f"[WARNING] Clamping concurrent downloads from {raw_threads} to {MAX_THREADS}.")
        raw_threads = MAX_THREADS

    scrape_reddit(subreddit_name, count, raw_threads, download_type, sort, resolve=resolve, use_resume=use_resume)


if __name__ == "__main__":
    main()
