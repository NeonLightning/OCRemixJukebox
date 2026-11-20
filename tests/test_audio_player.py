import os
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
    @mock.patch.object(ocremix.vlc, "Instance")
    def test_creates_player_with_supported_options(self, instance):
        player = ocremix.AudioPlayer()

        instance.assert_called_once_with("--quiet", "--verbose=-1")
        self.assertIs(player.player, instance.return_value.media_player_new.return_value)

    @mock.patch.object(ocremix.vlc, "Instance", return_value=None)
    def test_failed_instance_reports_initialization_error(self, instance):
        with self.assertRaisesRegex(ocremix.AudioInitializationError, "Could not initialize VLC"):
            ocremix.AudioPlayer()

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
