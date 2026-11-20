import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import vlc


class StreamProxy:
    def __init__(self, url, headers):
        self.url = url
        self.headers = dict(headers or {})
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.proxy_request(send_body=False)

            def do_GET(self):
                self.proxy_request(send_body=True)

            def proxy_request(self, send_body):
                headers = dict(proxy.headers)
                for name in ('Range', 'If-Range'):
                    if self.headers.get(name):
                        headers[name] = self.headers[name]
                try:
                    with requests.get(
                        proxy.url,
                        headers=headers,
                        stream=True,
                        timeout=(10, 30),
                        allow_redirects=True,
                    ) as response:
                        self.send_response(response.status_code)
                        for name in (
                            'Accept-Ranges', 'Content-Type', 'Content-Length',
                            'Content-Range', 'Last-Modified', 'ETag',
                        ):
                            if response.headers.get(name):
                                self.send_header(name, response.headers[name])
                        self.end_headers()
                        if send_body:
                            for chunk in response.iter_content(65536):
                                if chunk:
                                    try:
                                        self.wfile.write(chunk)
                                    except (BrokenPipeError, ConnectionResetError):
                                        break
                except requests.RequestException:
                    self.send_error(502, "Upstream stream is unavailable")

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.local_url = f"http://127.0.0.1:{self.server.server_port}/stream"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class AudioInitializationError(RuntimeError):
    pass


class AudioPlayer:
    def __init__(self):
        self.instance = vlc.Instance('--quiet', '--verbose=-1')
        if self.instance is None:
            raise AudioInitializationError(
                "Could not initialize VLC. Check that VLC and its plugins are installed correctly."
            )
        self.player = self.instance.media_player_new()
        self.current_url = None
        self.length = 0
        self.paused = False
        self.stream_proxy = None

    def play_stream(self, url, duration=None, http_headers=None):
        self.stop()
        if not url:
            raise AudioInitializationError("VLC cannot play an empty stream URL.")
        playback_url = url
        if not re.search(r'\.(?:m3u8|mpd)(?:\?|$)', url, re.I):
            self.stream_proxy = StreamProxy(url, http_headers)
            playback_url = self.stream_proxy.local_url
        media = self.instance.media_new(playback_url)
        if media is None:
            raise AudioInitializationError("VLC could not create a media source for this track.")
        for header, option in (
            ('User-Agent', ':http-user-agent='),
            ('Referer', ':http-referrer='),
            ('Cookie', ':http-cookies='),
        ):
            value = (http_headers or {}).get(header)
            if value:
                media.add_option(f"{option}{value}")
        self.player.set_media(media)
        if self.player.play() == -1:
            self.stop()
            raise AudioInitializationError("VLC could not start playback for this track.")
        self.current_url = url
        self.length = duration or 0
        self.paused = False

    def pause(self):
        state = self.player.get_state()
        if state == vlc.State.Playing:
            self.player.pause()
            self.paused = True
        elif self.paused or state == vlc.State.Paused:
            self.player.play()
            self.paused = False

    def stop(self):
        self.player.stop()
        if self.stream_proxy:
            self.stream_proxy.close()
            self.stream_proxy = None
        self.current_url = None
        self.paused = False
        self.length = 0

    def set_volume(self, volume):
        self.player.audio_set_volume(max(0, min(100, int(volume))))

    def get_pos(self):
        milliseconds = self.player.get_time()
        return milliseconds / 1000.0 if milliseconds >= 0 else -1

    def get_length(self):
        milliseconds = self.player.get_length()
        return milliseconds / 1000.0 if milliseconds > 0 else max(0, self.length)

    def is_playing(self):
        return self.player.is_playing() == 1

    def seek(self, fraction):
        length_ms = self.player.get_length()
        if length_ms > 0:
            fraction = max(0.0, min(1.0, fraction))
            self.player.set_time(int(fraction * length_ms))
