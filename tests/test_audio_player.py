import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from unittest import mock


with tempfile.TemporaryDirectory() as data_dir:
    original_directory = os.getcwd()
    try:
        os.chdir(data_dir)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": data_dir}):
            import ocremix
    finally:
        os.chdir(original_directory)


class AudioPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ocremix.QApplication.instance() or ocremix.QApplication([])

    @mock.patch.object(ocremix.vlc, "Instance")
    def test_creates_player_with_supported_options(self, instance):
        player = ocremix.AudioPlayer()

        instance.assert_called_once_with("--quiet", "--verbose=-1")
        self.assertIs(player.player, instance.return_value.media_player_new.return_value)

    @mock.patch.object(ocremix.vlc, "Instance", return_value=None)
    def test_failed_instance_reports_initialization_error(self, instance):
        with self.assertRaisesRegex(ocremix.AudioInitializationError, "Could not initialize VLC"):
            ocremix.AudioPlayer()

    @mock.patch.object(ocremix.vlc, "Instance")
    def test_play_stream_refuses_empty_url(self, instance):
        with self.assertRaisesRegex(ocremix.AudioInitializationError, "empty stream URL"):
            ocremix.AudioPlayer().play_stream("")

    @mock.patch.object(ocremix.vlc, "Instance")
    def test_play_stream_checks_vlc_result(self, instance):
        instance.return_value.media_player_new.return_value.play.return_value = -1
        with self.assertRaisesRegex(ocremix.AudioInitializationError, "could not start playback"):
            ocremix.AudioPlayer().play_stream("https://example.test/audio.mp3")

    @mock.patch.object(ocremix.vlc, "Instance")
    def test_paused_position_remains_available(self, instance):
        player = ocremix.AudioPlayer()
        player.player.get_time.return_value = 42000
        self.assertEqual(player.get_pos(), 42.0)

    @mock.patch.object(ocremix.vlc, "Instance")
    def test_play_stream_passes_provider_headers_to_vlc(self, instance):
        player = ocremix.AudioPlayer()
        media = instance.return_value.media_new.return_value

        player.play_stream("https://example.test/audio", 10, {
            "User-Agent": "provider-agent",
            "Referer": "https://example.test/source",
        })

        media.add_option.assert_any_call(":http-user-agent=provider-agent")
        media.add_option.assert_any_call(":http-referrer=https://example.test/source")

    @mock.patch.object(ocremix.requests, "get")
    @mock.patch.object(ocremix.yt_dlp, "YoutubeDL")
    def test_stream_resolution_prefers_progressive_audio_and_preserves_headers(self, youtube_dl, request_get):
        request_get.return_value.__enter__.return_value.status_code = 206
        request_get.return_value.__enter__.return_value.iter_content.return_value = iter([b"audio"])
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "duration": 123,
            "http_headers": {"Referer": "https://soundcloud.com/source"},
            "formats": [
                {"url": "hls", "acodec": "opus", "vcodec": "none", "abr": 160, "protocol": "m3u8_native"},
                {"url": "mp3", "acodec": "mp3", "vcodec": "none", "abr": 128, "protocol": "https"},
            ],
        }

        stream, duration, headers = ocremix.get_stream_url("https://soundcloud.com/source")

        self.assertEqual((stream, duration), ("mp3", 123))
        self.assertEqual(headers["Referer"], "https://soundcloud.com/source")
        self.assertIn("User-Agent", headers)

    @mock.patch.object(ocremix.requests, "head")
    @mock.patch.object(ocremix, "find_media_url", return_value="https://youtube.com/watch?v=test")
    def test_track_source_validation_does_not_resolve_stream(self, find_media, request_head):
        request_head.return_value.__enter__.return_value.status_code = 200
        track = {"url": "https://ocremix.org/song/1", "preview_url": ""}

        source = ocremix.validate_track_source(track)

        self.assertEqual(source, "https://youtube.com/watch?v=test")
        self.assertEqual(track["media_url"], source)

    @mock.patch.object(ocremix.requests, "get")
    @mock.patch.object(ocremix.yt_dlp, "YoutubeDL")
    def test_stream_resolution_rejects_unreadable_format(self, youtube_dl, request_get):
        blocked = mock.MagicMock()
        blocked.__enter__.return_value.status_code = 403
        blocked.__enter__.return_value.iter_content.return_value = iter([b""])
        readable = mock.MagicMock()
        readable.__enter__.return_value.status_code = 206
        readable.__enter__.return_value.iter_content.return_value = iter([b"audio"])
        request_get.side_effect = [blocked, readable]
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "duration": 10,
            "formats": [
                {"format_id": "140", "url": "https://example.test/bad.m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 128, "protocol": "https", "ext": "m4a"},
                {"format_id": "139", "url": "https://example.test/good.m4a", "acodec": "mp4a.40.5", "vcodec": "none", "abr": 48, "protocol": "https", "ext": "m4a"},
            ],
        }

        stream, _, _ = ocremix.get_stream_url("https://youtube.com/watch?v=test")

        self.assertEqual(stream, "https://example.test/good.m4a")

    def test_media_provider_deadline_rejects_stalled_task(self):
        with self.assertRaises(TimeoutError):
            ocremix.media.run_with_timeout(lambda: __import__('time').sleep(0.05), 0.01)

    def test_media_url_filter_rejects_arbitrary_external_page(self):
        self.assertFalse(ocremix.is_supported_media_url("https://example.test/help/privacy"))
        self.assertTrue(ocremix.is_supported_media_url("https://cdn.example.test/song.mp3?token=123"))

    @mock.patch.object(ocremix.os.path, "isfile")
    def test_plain_cookies_support_local_export(self, isfile):
        isfile.side_effect = lambda path: path.endswith("cookies.txt")

        self.assertTrue(ocremix.load_plain_cookies().endswith("cookies.txt"))

    def test_original_track_filter_only_hides_originals(self):
        window = mock.Mock(spec=ocremix.OCRemixWindow)
        window.add_browser_track_widget = ocremix.OCRemixWindow.add_browser_track_widget.__get__(window)
        window.hide_originals = True
        window.selected_game_id = "game"
        added = []
        window.browser_empty_label = mock.Mock()
        window.browser_container = mock.Mock()
        window.browser_tracks_layout = mock.Mock()
        window.browsable_tracks = {"game": [{"type": "original"}, {"type": "remix"}]}
        window.browser_summary = mock.Mock()
        with mock.patch.object(ocremix, "TrackWidget") as widget:
            window.add_browser_track_widget("game", {"type": "original", "url": "original"})
            window.add_browser_track_widget("game", {"title": "Remix", "type": "remix", "url": "remix"})
        self.assertEqual(widget.call_count, 1)
        added.append(widget.call_args.args[0]['type'])
        self.assertEqual(added, ["remix"])

    @mock.patch.object(ocremix.QDesktopServices, "openUrl")
    def test_track_title_click_does_not_return_qt_result(self, open_url):
        track = {"title": "Test", "artist": "Artist", "url": "https://example.test/track"}
        widget = ocremix.TrackWidget(track, "game")
        event = mock.Mock()

        result = widget.open_track_page(event)

        self.assertIsNone(result)
        event.accept.assert_called_once_with()
        open_url.assert_called_once()

    @mock.patch("builtins.print")
    @mock.patch.object(ocremix, "get_machine_key", return_value=b"key")
    @mock.patch.object(ocremix, "encrypted_cookies_file")
    @mock.patch.object(ocremix.os.path, "exists", return_value=True)
    @mock.patch.object(ocremix, "open", mock.mock_open(read_data=b"encrypted"), create=True)
    @mock.patch.object(ocremix, "Fernet")
    def test_blank_cookie_error_is_handled_without_trace(self, fernet, exists, path, key, output):
        fernet.return_value.decrypt.side_effect = Exception()

        self.assertIsNone(ocremix.decrypt_cookies_auto())
        self.assertIn("could not be decrypted", ocremix.cookie_load_error)
        output.assert_not_called()

    @mock.patch.object(ocremix.QMessageBox, "critical")
    @mock.patch.object(ocremix, "OCRemixWindow", side_effect=ocremix.AudioInitializationError("VLC failed"))
    @mock.patch.object(ocremix, "apply_system_theme")
    @mock.patch.object(ocremix, "QApplication")
    def test_main_reports_initialization_failure(self, application, theme, window, critical):
        self.assertEqual(ocremix.main(), 1)
        critical.assert_called_once_with(None, "Audio Initialization Error", "VLC failed")
        application.return_value.exec_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
