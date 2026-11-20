import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
import requests
import yt_dlp


soundcloud_cache = {}


def get_ydl_options(cookie_file=None):
    options = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 4,
        'retries': 1,
        'fragment_retries': 0,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        },
    }
    if cookie_file and os.path.exists(cookie_file):
        options['cookiefile'] = cookie_file
    return options


def run_with_timeout(callback, timeout):
    result = queue.Queue(maxsize=1)

    def worker():
        try:
            result.put((True, callback()))
        except BaseException as error:
            result.put((False, error))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"media provider did not respond within {timeout:.0f} seconds")
    succeeded, value = result.get_nowait()
    if not succeeded:
        raise value
    return value


def extract_info(media_url, cookie_file=None):
    def extract():
        with yt_dlp.YoutubeDL(get_ydl_options(cookie_file)) as ydl:
            return ydl.extract_info(media_url, download=False)

    return run_with_timeout(extract, 30)


def get_stream_url(media_url, retry_count=5, cookie_file=None):
    for attempt in range(retry_count):
        try:
            info = extract_info(media_url, cookie_file=cookie_file)
            duration = info.get('duration', 0)
            formats = info.get('formats', [])
            audio_formats = [
                item for item in formats
                if item.get('url') and item.get('acodec') != 'none' and item.get('vcodec') == 'none'
            ]
            progressive = [item for item in audio_formats if item.get('protocol') not in ('m3u8', 'm3u8_native')]
            compatible = [
                item for item in progressive
                if item.get('ext') in ('m4a', 'mp3', 'aac', 'mp4')
                or str(item.get('acodec', '')).startswith('mp4a')
            ]
            candidates = sorted(
                compatible or progressive or audio_formats or [info],
                key=lambda item: item.get('abr', 0) or 0,
                reverse=True,
            )[:4]

            def probe(selected):
                stream_url = selected.get('url')
                if not stream_url:
                    raise ValueError("format has no URL")
                headers = {
                    str(key).title(): value
                    for key, value in (selected.get('http_headers') or info.get('http_headers') or {}).items()
                }
                headers.setdefault('User-Agent', get_ydl_options(cookie_file)['http_headers']['User-Agent'])
                with requests.get(
                    stream_url,
                    headers={**headers, 'Range': 'bytes=0-1023'},
                    stream=True,
                    timeout=(3, 6),
                ) as response:
                    chunk = next(response.iter_content(1024), b'')
                if response.status_code not in (200, 206) or not chunk:
                    raise ValueError(f"HTTP {response.status_code}")
                return stream_url, duration, headers

            failures = []
            with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
                future_map = {executor.submit(probe, candidate): candidate for candidate in candidates}
                for future in as_completed(future_map):
                    selected = future_map[future]
                    try:
                        return future.result()
                    except Exception as error:
                        failures.append(f"{selected.get('format_id', 'unknown')} {error}")
            detail = ", ".join(failures[:4]) or "no candidate formats"
            raise ValueError(f"No readable audio stream found ({detail})")
        except Exception as error:
            print(f"Stream error (attempt {attempt + 1}): {error}")
            if '429' not in str(error):
                break
            time.sleep(min(3 * (2 ** attempt), 15))
    raise RuntimeError("Failed to extract stream after retries")


def get_playlist_tracks(playlist_url, retry=5, cookie_file=None):
    if playlist_url in soundcloud_cache:
        return soundcloud_cache[playlist_url]
    options = get_ydl_options(cookie_file)
    options.update({'extract_flat': False, 'playlistend': 100})
    for attempt in range(retry):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                tracks = []
                for entry in info.get('entries', []):
                    if not entry:
                        continue
                    track_url = entry.get('webpage_url') or entry.get('url')
                    if track_url:
                        tracks.append({
                            'title': entry.get('track') or entry.get('title') or 'Unknown Title',
                            'artist': entry.get('artist') or entry.get('uploader') or entry.get('creator') or 'Unknown Artist',
                            'url': track_url,
                            'preview_url': track_url,
                            'type': 'album',
                            'album_name': None,
                            'duration': entry.get('duration', 0),
                        })
            soundcloud_cache[playlist_url] = tracks
            return tracks
        except Exception as error:
            print(f"Playlist error (attempt {attempt + 1}): {error}")
            if '429' not in str(error):
                break
            time.sleep(min(3 * (2 ** attempt), 15))
    return []


def is_supported_media_url(media_url):
    if not isinstance(media_url, str):
        return False
    parsed = urlparse(media_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False
    host = parsed.hostname or ''
    if host.endswith(('youtube.com', 'youtu.be', 'soundcloud.com', 'bandcamp.com')):
        return True
    return parsed.path.lower().endswith(
        ('.mp3', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.wav', '.m3u8', '.mpd', '.mp4')
    )


def find_media_url(page_url):
    if is_supported_media_url(page_url):
        return page_url
    try:
        response = requests.get(page_url, headers={"User-Agent": "OCReMixJukebox/1.0"}, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return None
    soup = BeautifulSoup(response.text, 'html.parser')
    direct = soup.find('a', href=re.compile(r'\.(?:mp3|m4a|ogg|opus|flac)$', re.I))
    if direct:
        return urljoin(page_url, direct['href'])
    for iframe in soup.find_all('iframe', src=True):
        source = iframe['src']
        if 'youtube.com/embed/' in source or 'youtu.be/' in source:
            return f"https://www.youtube.com/watch?v={source.split('/')[-1].split('?')[0]}"
        if 'soundcloud.com/player' in source:
            embedded = parse_qs(urlparse(source).query).get('url', [''])[0]
            if embedded:
                return unquote(embedded)
        if is_supported_media_url(source):
            return source
    preview = soup.find('a', {'data-preview': True})
    if preview and preview.get('data-preview'):
        return preview['data-preview']
    audio = soup.find('audio')
    if audio:
        source = audio.get('src') or (audio.find('source') and audio.find('source').get('src'))
        if source:
            return urljoin(page_url, source)
    for link in soup.find_all('a', href=True):
        candidate = urljoin(page_url, link['href'])
        if is_supported_media_url(candidate):
            return candidate
    return None


def is_soundcloud_profile(url):
    if 'soundcloud.com/' in url:
        parts = url.split('soundcloud.com/')[-1].strip('/').split('/')
        return len(parts) == 1 and bool(parts[0]) and not parts[0].startswith('?')
    return False
