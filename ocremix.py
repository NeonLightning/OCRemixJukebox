#!/usr/bin/env python3
import sys, os, re, json, time, threading, random, tempfile, base64, uuid, shutil, subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from urllib.parse import urljoin, urlencode, unquote, urlparse, parse_qs
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLineEdit, QPushButton, QLabel, QSlider, QScrollArea, QListWidget,
    QMessageBox, QFileDialog, QSystemTrayIcon, QMenu, QAction, QFrame, QStyleFactory)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QUrl
from PyQt5.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices
try:
    from pynput import keyboard as pynput_keyboard
except (ImportError, OSError):
    pynput_keyboard = None
import requests, yt_dlp, vlc
from jukebox_audio import AudioInitializationError, AudioPlayer, StreamProxy
import jukebox_media as media
from jukebox_widgets import GameWidget, TrackWidget

os.environ['VLC_VERBOSE'] = '-1'

cookies_path = None

def get_app_data_dir():
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    path = os.path.join(base, 'OCRemixJukebox')
    os.makedirs(path, exist_ok=True)
    return path

app_data_dir = get_app_data_dir()
encrypted_cookies_file = os.path.join(app_data_dir, "soundcloud_cookies.enc")
plain_cookies_file = os.path.join(app_data_dir, "soundcloud_cookies_plain.txt")
temp_cookies_file = None
cookie_load_error = None

def migrate_legacy_file(filename, destination):
    if not os.path.exists(destination) and os.path.isfile(filename):
        try:
            shutil.copy2(filename, destination)
        except OSError:
            pass

migrate_legacy_file("soundcloud_cookies.enc", encrypted_cookies_file)
migrate_legacy_file("soundcloud_cookies_plain.txt", plain_cookies_file)

# ---------- System theme ----------
def is_windows_dark_mode():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False

def is_linux_dark_mode():
    gtk_theme = os.environ.get("GTK_THEME", "").lower()
    if "dark" in gtk_theme:
        return True
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and "dark" in result.stdout.lower():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and "dark" in result.stdout.lower():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    kde_globals = os.path.expanduser("~/.config/kdeglobals")
    if os.path.isfile(kde_globals):
        try:
            with open(kde_globals, "r", encoding="utf-8") as file:
                return "colortext" in file.read().lower()
        except OSError:
            pass
    return False

def is_dark_mode():
    if sys.platform.startswith("win"):
        return is_windows_dark_mode()
    if sys.platform.startswith("linux"):
        return is_linux_dark_mode()
    return False

def apply_system_theme(app):
    if is_dark_mode():
        app.setStyle(QStyleFactory.create("Fusion"))
        dark = QPalette()
        dark.setColor(QPalette.Window, QColor(53,53,53))
        dark.setColor(QPalette.WindowText, Qt.white)
        dark.setColor(QPalette.Base, QColor(25,25,25))
        dark.setColor(QPalette.AlternateBase, QColor(53,53,53))
        dark.setColor(QPalette.ToolTipBase, Qt.black)
        dark.setColor(QPalette.ToolTipText, Qt.white)
        dark.setColor(QPalette.Text, Qt.white)
        dark.setColor(QPalette.Button, QColor(53,53,53))
        dark.setColor(QPalette.ButtonText, Qt.white)
        dark.setColor(QPalette.BrightText, Qt.red)
        dark.setColor(QPalette.Link, QColor(42,130,218))
        dark.setColor(QPalette.Highlight, QColor(42,130,218))
        dark.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(dark)
    else:
        app.setStyle("Fusion")
        app.setPalette(app.style().standardPalette())

# ---------- Cookie encryption ----------
def get_machine_key():
    if sys.platform.startswith("linux"):
        for machine_id_path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(machine_id_path, "rb") as f:
                    machine_id = f.read().strip()
                if machine_id:
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=32,
                        salt=b"ocremix_salt_2025",
                        iterations=100000,
                    )
                    return base64.urlsafe_b64encode(kdf.derive(machine_id))
            except OSError:
                pass
    mac = uuid.getnode()
    if mac in (0xffffffffffff, 0):
        key_file = os.path.join(app_data_dir, "machine_key.bin")
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                return f.read()
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        return key
    mac_str = str(mac)
    salt = b"ocremix_salt_2025"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
    return base64.urlsafe_b64encode(kdf.derive(mac_str.encode()))

def encrypt_cookies_auto(plain_path):
    try:
        with open(plain_path, 'rb') as f:
            plain = f.read()
        fernet = Fernet(get_machine_key())
        encrypted = fernet.encrypt(plain)
        with open(encrypted_cookies_file, 'wb') as f:
            f.write(encrypted)
        return True
    except Exception as e:
        QMessageBox.critical(None, "Encryption Error", f"Failed to encrypt cookies: {e}")
        return False

def decrypt_cookies_auto():
    global temp_cookies_file, cookie_load_error
    cookie_load_error = None
    if not os.path.exists(encrypted_cookies_file):
        return None
    try:
        with open(encrypted_cookies_file, 'rb') as f:
            encrypted = f.read()
        plain = Fernet(get_machine_key()).decrypt(encrypted)
        temp_cookies_file = tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt')
        temp_cookies_file.write(plain)
        temp_cookies_file.close()
        return temp_cookies_file.name
    except Exception as error:
        detail = str(error).strip()
        cookie_load_error = "Saved encrypted cookies could not be decrypted on this machine. Re-import them from File → Manage SoundCloud Cookies."
        if detail:
            print(f"Encrypted cookie load skipped: {detail}")
        return None

def save_plain_cookies(source_path):
    try:
        shutil.copy2(source_path, plain_cookies_file)
        return True
    except Exception as e:
        QMessageBox.critical(None, "Error", f"Failed to save plain cookies: {e}")
        return False

def load_plain_cookies():
    candidates = (
        plain_cookies_file,
        os.path.abspath("soundcloud_cookies.txt"),
        os.path.abspath("cookies.txt"),
    )
    return next((path for path in candidates if os.path.isfile(path)), None)

# ---------- Media resolution ----------
def get_ydl_options():
    return media.get_ydl_options(cookies_path)


def get_stream_url(media_url, retry_count=5):
    return media.get_stream_url(media_url, retry_count=retry_count, cookie_file=cookies_path)


def get_playlist_tracks(playlist_url, retry=5):
    return media.get_playlist_tracks(playlist_url, retry=retry, cookie_file=cookies_path)


def is_supported_media_url(media_url):
    return media.is_supported_media_url(media_url)


def find_media_url(page_url):
    return media.find_media_url(page_url)


def is_soundcloud_profile(url):
    return media.is_soundcloud_profile(url)


def validate_track_source(track):
    preview = track.get('preview_url', '')
    media_url = preview if preview and not is_soundcloud_profile(preview) else find_media_url(track.get('url', ''))
    if not is_supported_media_url(media_url):
        raise ValueError("No supported media URL was found on the track page")
    with requests.head(media_url, allow_redirects=True, timeout=(3, 5)) as response:
        if response.status_code >= 400:
            raise ValueError(f"Media provider rejected the source URL (HTTP {response.status_code})")
    track['media_url'] = media_url
    return media_url


def resolve_track_media(track, retry_count=2):
    preview = track.get('preview_url', '')
    media_url = track.get('media_url') or (
        preview if preview and not is_soundcloud_profile(preview)
        else find_media_url(track.get('url', ''))
    )
    if not is_supported_media_url(media_url):
        raise ValueError("No supported media URL was found on the track page")
    track['media_url'] = media_url
    stream_url, duration, headers = get_stream_url(media_url, retry_count=retry_count)
    parsed_stream = urlparse(stream_url) if isinstance(stream_url, str) else None
    if not parsed_stream or parsed_stream.scheme not in ('http', 'https') or not parsed_stream.netloc:
        raise ValueError("The resolved stream URL is not a valid HTTP URL")
    track['resolved_stream_url'] = stream_url
    track['resolved_headers'] = headers
    track['resolved_at'] = time.monotonic()
    track['resolved_duration'] = int(duration or track.get('duration') or 0)
    if track['resolved_duration'] > 0:
        track['duration'] = track['resolved_duration']
    return stream_url, track['resolved_duration'], headers


# ---------- Signals ----------
class UpdateSignals(QObject):
    append_track = pyqtSignal(str, dict)
    append_browser_track = pyqtSignal(str, dict)
    update_loading_count = pyqtSignal(str, int)
    remove_loading_task = pyqtSignal(str)
    status_message = pyqtSignal(str)
    play_button_text = pyqtSignal(str)
    start_playback = pyqtSignal(dict, str, int, dict, int)
    loading_display_update = pyqtSignal()
    skip_to_next = pyqtSignal()
    skip_global_shuffle_next = pyqtSignal()
    media_play_pause = pyqtSignal()
    media_next = pyqtSignal()
    media_previous = pyqtSignal()
    remove_invalid_track = pyqtSignal(str, dict)
    track_playback_failed = pyqtSignal(dict)
    refresh_title = pyqtSignal()
    update_tray_tooltip = pyqtSignal(str)

# ---------- Main Window ----------
class OCRemixWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OC ReMix Jukebox")
        self.resize(1100, 700)
        self.game_catalog = {}
        self.filtered_game_ids = []
        self.cache_file = os.path.join(app_data_dir, "games_cache.json")
        migrate_legacy_file("games_cache.json", self.cache_file)
        self.added_games = {}
        self.tracks_by_game = {}
        self.selected_game_id = None
        self.browsable_tracks = {}
        self.master_tracklist = []
        self.shuffle_queue = []
        self.shuffle_index = 0
        self.shuffle_active = False
        self.global_shuffle_active = False
        self.current_track = None
        self.play_history = []
        self.global_track_cache = {}
        self.repeat_mode = 0
        self.last_played_track = None
        self.player = AudioPlayer()
        self.play_request_id = 0
        self.playback_started_at = None
        self.loading_tasks = {}
        self.signals = UpdateSignals()
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress)
        self.progress_timer.start(500)
        self.global_stop = threading.Event()
        self.global_thread = None
        self.settings = QSettings("OCRemix", "Jukebox")
        self.auto_remove_invalid = self.settings.value("auto_remove_invalid", False, type=bool)
        self.hide_originals = self.settings.value("hide_originals", False, type=bool)
        self.auto_export_track = False
        self.export_file_path = None
        self.last_queue_path = self.settings.value("last_queue_path", "", type=str)

        self.setup_ui()
        self.apply_app_styles()
        self.signals.append_track.connect(self.add_track_widget)
        self.signals.append_browser_track.connect(self.add_browser_track_widget)
        self.signals.update_loading_count.connect(self.update_loading_count)
        self.signals.remove_loading_task.connect(self.remove_loading_task)
        self.signals.status_message.connect(self.status_label.setText)
        self.signals.play_button_text.connect(self.play_button.setText)
        self.signals.start_playback.connect(self.start_playback)
        self.signals.loading_display_update.connect(self.update_loading_display)
        self.signals.skip_to_next.connect(self.play_next_in_queue)
        self.signals.skip_global_shuffle_next.connect(self.global_shuffle_next)
        self.signals.media_play_pause.connect(self.on_play_pause)
        self.signals.media_next.connect(self.on_next)
        self.signals.media_previous.connect(self.on_previous)
        self.signals.remove_invalid_track.connect(self.remove_track)
        self.signals.track_playback_failed.connect(self.handle_playback_failure)
        self.signals.refresh_title.connect(self.refresh_title)
        self.signals.update_tray_tooltip.connect(self.update_tray_tooltip)

        self.setup_tray()
        self.setup_media_keys()
        self.load_cookies()
        self.load_games()
        self.volume_slider.wheelEvent = self.volume_slider_wheel

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # left panel
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        browse_title = QLabel("Browse games")
        browse_title.setObjectName("sectionTitle")
        left_layout.addWidget(browse_title)
        self.search_box = QLineEdit()
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setPlaceholderText("Search by game or platform…")
        self.search_box.setToolTip("Type to filter the game catalog")
        self.search_box.textChanged.connect(self.filter_games)
        left_layout.addWidget(self.search_box)
        self.game_list = QListWidget()
        self.game_list.itemClicked.connect(self.on_game_click)
        left_layout.addWidget(self.game_list)
        self.game_count_label = QLabel("0 games")
        self.game_count_label.setObjectName("mutedLabel")
        left_layout.addWidget(self.game_count_label)

        # right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_splitter = QSplitter(Qt.Vertical)
        browser_panel = QWidget()
        browser_layout = QVBoxLayout(browser_panel)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_header = QHBoxLayout()
        self.browser_title = QLabel("Select a game")
        self.browser_title.setObjectName("sectionTitle")
        browser_header.addWidget(self.browser_title)
        browser_header.addStretch()
        self.browser_summary = QLabel("No game selected")
        self.browser_summary.setObjectName("mutedLabel")
        browser_header.addWidget(self.browser_summary)
        browser_layout.addLayout(browser_header)
        self.browser_scroll = QScrollArea()
        self.browser_scroll.setWidgetResizable(True)
        self.browser_container = QWidget()
        self.browser_tracks_layout = QVBoxLayout(self.browser_container)
        self.browser_tracks_layout.setAlignment(Qt.AlignTop)
        self.browser_empty_label = QLabel("Select a game on the left to browse its tracks.")
        self.browser_empty_label.setObjectName("emptyState")
        self.browser_empty_label.setAlignment(Qt.AlignCenter)
        self.browser_tracks_layout.addWidget(self.browser_empty_label)
        self.browser_scroll.setWidget(self.browser_container)
        browser_layout.addWidget(self.browser_scroll)
        right_splitter.addWidget(browser_panel)

        queue_panel = QWidget()
        queue_panel_layout = QVBoxLayout(queue_panel)
        queue_panel_layout.setContentsMargins(0, 0, 0, 0)
        queue_header = QHBoxLayout()
        queue_title = QLabel("Queue")
        queue_title.setObjectName("sectionTitle")
        queue_header.addWidget(queue_title)
        queue_header.addStretch()
        self.queue_summary = QLabel("Queue empty")
        self.queue_summary.setObjectName("mutedLabel")
        queue_header.addWidget(self.queue_summary)
        queue_panel_layout.addLayout(queue_header)
        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_container = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_container)
        self.queue_layout.setAlignment(Qt.AlignTop)
        self.empty_queue_label = QLabel("Your queue is empty\n\nPress + on a track above to add it.")
        self.empty_queue_label.setObjectName("emptyState")
        self.empty_queue_label.setAlignment(Qt.AlignCenter)
        self.empty_queue_label.setMinimumHeight(220)
        self.queue_layout.addWidget(self.empty_queue_label)
        self.queue_scroll.setWidget(self.queue_container)
        queue_panel_layout.addWidget(self.queue_scroll)
        right_splitter.addWidget(queue_panel)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)
        right_layout.addWidget(right_splitter, 1)

        self.loading_label = QLabel()
        self.loading_label.setVisible(False)
        right_layout.addWidget(self.loading_label)

        # controls
        playback_bar = QHBoxLayout()
        self.shuffle_button = QPushButton("Shuffle queue")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setToolTip("Play the current queue in random order")
        self.shuffle_button.clicked.connect(self.toggle_shuffle)
        playback_bar.addWidget(self.shuffle_button)
        self.global_shuffle_button = QPushButton("Global shuffle")
        self.global_shuffle_button.setCheckable(True)
        self.global_shuffle_button.setToolTip("Discover and play tracks from the full catalog")
        self.global_shuffle_button.clicked.connect(self.toggle_global_shuffle)
        playback_bar.addWidget(self.global_shuffle_button)
        playback_bar.addStretch()
        self.prev_button = QPushButton("⏮")
        self.prev_button.setToolTip("Previous track")
        self.prev_button.clicked.connect(self.on_previous)
        playback_bar.addWidget(self.prev_button)
        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("primaryPlayButton")
        self.play_button.setToolTip("Play or pause")
        self.play_button.clicked.connect(self.on_play_pause)
        playback_bar.addWidget(self.play_button)
        self.next_button = QPushButton("⏭")
        self.next_button.setToolTip("Next track")
        self.next_button.clicked.connect(self.on_next)
        playback_bar.addWidget(self.next_button)
        self.repeat_button = QPushButton("🔁 Off")
        self.repeat_button.setToolTip("Change repeat mode")
        self.repeat_button.clicked.connect(self.toggle_repeat)
        playback_bar.addWidget(self.repeat_button)
        playback_bar.addWidget(QLabel("🔊"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.settings.value("volume", 70, type=int))
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.valueChanged.connect(self.change_volume)
        playback_bar.addWidget(self.volume_slider)
        right_layout.addLayout(playback_bar)

        timeline = QHBoxLayout()
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("mutedLabel")
        timeline.addWidget(self.elapsed_label)
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setToolTip("Seek through the current track")
        self.position_slider.sliderMoved.connect(self.seek)
        timeline.addWidget(self.position_slider, 1)
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("mutedLabel")
        timeline.addWidget(self.duration_label)
        right_layout.addLayout(timeline)

        self.status_label = QLabel("Ready")
        self.status_label.setCursor(Qt.PointingHandCursor)
        self.status_label.mousePressEvent = self.on_status_click
        self.statusBar().addWidget(self.status_label)

        # menu
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        file_menu.addAction("Refresh Games", self.refresh_games)
        file_menu.addAction("Save Queue", self.save_queue)
        file_menu.addAction("Load Queue", lambda: self.load_queue())
        reload_action = QAction("Reload Last Playlist", self)
        reload_action.triggered.connect(self.reload_last_queue)
        reload_action.setShortcut("Ctrl+Shift+L")
        file_menu.addAction(reload_action)
        file_menu.addAction("Clear Queue", self.clear_queue)
        file_menu.addSeparator()
        self.auto_remove_action = QAction("Auto‑remove invalid tracks", self, checkable=True)
        self.auto_remove_action.setChecked(self.auto_remove_invalid)
        self.auto_remove_action.triggered.connect(self.toggle_auto_remove)
        file_menu.addAction(self.auto_remove_action)
        self.hide_originals_action = QAction("Hide original [O] tracks", self, checkable=True)
        self.hide_originals_action.setChecked(self.hide_originals)
        self.hide_originals_action.triggered.connect(self.toggle_hide_originals)
        file_menu.addAction(self.hide_originals_action)
        self.auto_export_action = QAction("Auto‑export current track to file", self, checkable=True)
        self.auto_export_action.triggered.connect(self.toggle_auto_export)
        file_menu.addAction(self.auto_export_action)
        file_menu.addAction("Export current track info (one‑time)", lambda: self.export_track_info())
        file_menu.addSeparator()
        file_menu.addAction("Manage SoundCloud Cookies", self.manage_cookies)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.quit_app)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)
        central.setLayout(main_layout)

    def apply_app_styles(self):
        dark = is_dark_mode()
        background = "#1e1f22" if dark else "#f7f7f9"
        surface = "#2b2d31" if dark else "#ffffff"
        border = "#41454c" if dark else "#d8dbe1"
        text = "#f3f4f6" if dark else "#202124"
        muted = "#aeb2ba" if dark else "#686b73"
        accent = "#56a8ff"
        self.setStyleSheet(f"""
            QMainWindow {{ background: {background}; color: {text}; }}
            QWidget {{ color: {text}; }}
            QLineEdit, QListWidget, QScrollArea {{
                background: {surface}; border: 1px solid {border}; border-radius: 8px;
            }}
            QLineEdit {{ padding: 8px; selection-background-color: {accent}; }}
            QListWidget::item {{ padding: 7px 8px; border-radius: 5px; }}
            QListWidget::item:selected {{ background: {accent}; color: #101418; }}
            QPushButton {{
                background: {surface}; border: 1px solid {border}; border-radius: 7px;
                padding: 6px 10px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:checked {{ background: {accent}; color: #101418; border-color: {accent}; }}
            QPushButton#primaryPlayButton {{
                background: {accent}; color: #101418; border-color: {accent};
                min-width: 42px; min-height: 34px; font-weight: bold; font-size: 17px;
            }}
            QLabel#sectionTitle {{ font-size: 16px; font-weight: 700; padding: 2px; }}
            QLabel#mutedLabel {{ color: {muted}; }}
            QLabel#emptyState {{ color: {muted}; font-size: 14px; padding: 40px; }}
            QFrame[active="true"] {{ background: rgba(86, 168, 255, 0.12); border: 1px solid {accent}; border-radius: 7px; }}
            QStatusBar {{ border-top: 1px solid {border}; }}
        """)

    # ---------- Settings ----------
    def toggle_auto_remove(self, checked):
        self.auto_remove_invalid = checked
        self.settings.setValue("auto_remove_invalid", checked)

    def toggle_hide_originals(self, checked):
        self.hide_originals = checked
        self.settings.setValue("hide_originals", checked)
        if self.selected_game_id is not None:
            self.show_browsable_tracks(self.selected_game_id)

    def toggle_auto_export(self, checked):
        self.auto_export_track = checked
        if checked and not self.export_file_path:
            path, _ = QFileDialog.getSaveFileName(self, "Select file for auto‑export", "", "Text files (*.txt)")
            if path:
                self.export_file_path = path
                self.status_label.setText(f"Auto‑export enabled → {os.path.basename(path)}")
            else:
                self.auto_export_action.setChecked(False)
                self.auto_export_track = False
        elif not checked:
            self.export_file_path = None

    def export_track_info(self, track=None):
        track = track or self.current_track
        if not track:
            QMessageBox.information(self, "Export Track", "No track is currently playing.")
            return
        game_name = track.get('game_name', 'Unknown Game')
        track_name = track.get('title', 'Unknown Title')
        artist = track.get('artist', 'Unknown Artist')
        if track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
            artist = artist[:11] + ' ' + artist[11:]
        url = track.get('url', '')
        file_path = self.export_file_path if self.auto_export_track and self.export_file_path else QFileDialog.getSaveFileName(self, "Save Track Info", "", "Text files (*.txt)")[0]
        if not file_path:
            return
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"Game: {game_name}\nTrack: {track_name}\nArtist: {artist}\nURL: {url}\n")
            if not self.auto_export_track:
                self.status_label.setText(f"Track info saved to {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save track info: {e}")

    def reload_last_queue(self):
        if self.last_queue_path and os.path.exists(self.last_queue_path):
            self.load_queue(self.last_queue_path)
        else:
            QMessageBox.warning(self, "Reload Last Playlist", "No previously saved queue found or file missing.")

    def volume_slider_wheel(self, event):
        delta = event.angleDelta().y()
        new_val = self.volume_slider.value() + (5 if delta > 0 else -5)
        self.volume_slider.setValue(max(0, min(100, new_val)))

    def refresh_title(self):
        count = len(self.master_tracklist)
        self.empty_queue_label.setVisible(not self.added_games)
        self.queue_summary.setText(
            f"{count} track{'s' if count != 1 else ''} · {len(self.added_games)} game{'s' if len(self.added_games) != 1 else ''}"
            if count else "Queue empty"
        )
        for widget in self.queue_container.findChildren(TrackWidget):
            active = self.current_track is not None and widget.track['url'] == self.current_track.get('url')
            widget.setProperty("active", active)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if self.current_track:
            game = self.current_track.get('game_name', '')
            title = self.current_track.get('title', '')
            artist = self.current_track.get('artist', '')
            if artist.lower().startswith('arranged by') and len(artist) > 11 and artist[11:12] != ' ':
                artist = artist[:11] + ' ' + artist[11:]
            self.setWindowTitle(f"{count} || {game} || {title} || {artist}" if game else f"{count} || {title} || {artist}")
        else:
            self.setWindowTitle(f"OC ReMix Jukebox || {count}")

    def update_tray_tooltip(self, text):
        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.setToolTip(text)

    # ---------- Game catalog ----------
    def load_games(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.game_catalog = json.load(f)
                self.filter_games()
                self.status_label.setText(f"Loaded {len(self.game_catalog)} games from cache")
                self.adjust_game_list_width()
            except:
                self.status_label.setText("Cache error, refreshing...")
                self.refresh_games()
        else:
            self.status_label.setText("No cache found. Click Refresh Games to build catalog.")
            self.filter_games()

    def refresh_games(self):
        self.status_label.setText("Scraping all games... (may take a minute)")
        def worker():
            try:
                games = self.scrape_all_games()
                self.game_catalog = games
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(games, f, ensure_ascii=False, indent=2)
                self.signals.status_message.emit(f"Loaded {len(games)} games from OCRemix")
                QTimer.singleShot(0, self.filter_games)
                QTimer.singleShot(0, self.adjust_game_list_width)
            except Exception as e:
                self.signals.status_message.emit(f"Scraping error: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def scrape_all_games(self):
        headers = {"User-Agent": "OCReMixJukebox/1.0"}
        base_url = "https://ocremix.org/games/?"
        all_games = {}
        filters = ["ltr-0"] + [f"ltr-{chr(c)}" for c in range(ord('a'), ord('z')+1)]
        for filt in filters:
            offset = 0
            while True:
                url = base_url + urlencode({"sort": "", "filter": filt, "offset": offset})
                resp = requests.get(url, headers=headers)
                if resp.status_code != 200:
                    break
                soup = BeautifulSoup(resp.text, 'html.parser')
                links = soup.find_all('a', href=lambda h: h and '/game/' in h and '/remix/' not in h)
                if not links:
                    break
                for a in links:
                    name = a.get_text(strip=True)
                    if not name:
                        continue
                    href = a['href']
                    parts = href.strip('/').split('/')
                    if len(parts) >= 2 and parts[0] == 'game':
                        gid = parts[1]
                        game_url = urljoin(url, href)
                        platform = ""
                        parent = a.find_parent('div', class_=re.compile(r'game-card|card'))
                        if parent:
                            plat = parent.find('span', class_=re.compile(r'platform'))
                            if plat:
                                platform = plat.get_text(strip=True)
                        if not platform:
                            slug = parts[-1] if len(parts) > 2 else ""
                            platform = slug.rsplit('-', 1)[-1].upper() if '-' in slug else ""
                            if platform and platform[0].isdigit():
                                platform = ""
                        display = f"{name} ({platform})" if platform else name
                        all_games[gid] = {"name": display, "url": game_url, "platform": platform}
                if len(links) < 50:
                    break
                offset += 50
        return all_games

    def filter_games(self):
        query = self.search_box.text().lower().strip()
        self.filtered_game_ids = [gid for gid, data in self.game_catalog.items() if query in data['name'].lower()] if self.game_catalog else []
        self.game_list.clear()
        for gid in self.filtered_game_ids:
            self.game_list.addItem(self.game_catalog[gid]['name'])
        shown = len(self.filtered_game_ids)
        total = len(self.game_catalog)
        self.game_count_label.setText(f"{shown} of {total} games" if query else f"{total} games")

    def adjust_game_list_width(self):
        if self.game_catalog:
            max_len = max((len(data['name']) for data in self.game_catalog.values()), default=0)
            self.game_list.setMinimumWidth(min(max_len * 9 + 30, 350))

    def on_game_click(self, item):
        idx = self.game_list.row(item)
        if 0 <= idx < len(self.filtered_game_ids):
            self.add_game(self.filtered_game_ids[idx])

    # ---------- Adding games ----------
    def add_game(self, game_id):
        self.selected_game_id = game_id
        info = self.game_catalog[game_id]
        self.browser_title.setText(info['name'])
        self.clear_browser_tracks()
        cached = self.browsable_tracks.get(game_id)
        if cached is not None:
            self.show_browsable_tracks(game_id)
            return
        self.browsable_tracks[game_id] = []
        self.browser_empty_label.setText("Loading and validating tracks…")
        self.browser_empty_label.show()
        self.browser_summary.setText("Loading…")
        self.add_loading_task(game_id, info['name'])
        self.status_label.setText(f"Discovering tracks for {info['name']}…")
        threading.Thread(target=self.load_tracks_incremental, args=(game_id,), daemon=True).start()

    def clear_browser_tracks(self):
        while self.browser_tracks_layout.count():
            item = self.browser_tracks_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.browser_empty_label = QLabel("No playable tracks found for this game.")
        self.browser_empty_label.setObjectName("emptyState")
        self.browser_empty_label.setAlignment(Qt.AlignCenter)
        self.browser_tracks_layout.addWidget(self.browser_empty_label)

    def load_tracks_incremental(self, game_id):
        url = self.game_catalog[game_id]['url']
        headers = {"User-Agent": "OCReMixJukebox/1.0"}
        main = url.rstrip('/')
        count = 0
        rejected = 0
        discovered = 0
        pending = set()
        game_name = self.game_catalog[game_id]['name']

        def report_progress(active, completed):
            self.signals.status_message.emit(
                f"Validating {game_name}: {completed} checked, {active} in progress…"
            )

        def collect(future):
            nonlocal count, rejected
            try:
                track = future.result()
            except Exception as error:
                rejected += 1
                print(f"Skipping unavailable track: {error}")
                report_progress(max(0, len(pending) - 1), count + rejected)
                return
            self.browsable_tracks.setdefault(game_id, []).append(track)
            count += 1
            report_progress(max(0, len(pending) - 1), count + rejected)
            self.signals.append_browser_track.emit(game_id, track)
            self.signals.update_loading_count.emit(game_id, count)
            self.signals.refresh_title.emit()

        def validate(track):
            track.update({'game_id': game_id, 'game_name': game_name, 'duration': track.get('duration', 0)})
            resolve_track_media(track, retry_count=1)
            return track

        def drain_all(executor):
            for future in as_completed(tuple(pending)):
                collect(future)
                pending.discard(future)

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="track-check") as executor:
            def submit(track):
                nonlocal discovered
                discovered += 1
                future = executor.submit(validate, track)
                pending.add(future)
                report_progress(len(pending), count + rejected)
                if len(pending) >= 4:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for completed in done:
                        collect(completed)
                        pending.discard(completed)

            try:
                for track in self.scrape_stream(main, '/song/', headers):
                    submit(track)
            except Exception as error:
                print(f"Originals error: {error}")
            try:
                for track in self.scrape_stream(main + '/remixes', '/remix/', headers):
                    submit(track)
            except Exception as error:
                print(f"Remixes error: {error}")
            try:
                response = requests.get(main, headers=headers, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                albums = []
                for link in soup.find_all('a', href=lambda href: href and '/album/' in href):
                    parent = link.find_parent('div', class_='widget-neutral')
                    if parent and 'Promotion' in parent.get_text():
                        continue
                    albums.append((urljoin(main, link['href']), link.get_text(strip=True)))
                for index, (album_url, album_name) in enumerate(albums):
                    if index > 0:
                        time.sleep(2)
                    try:
                        for track in self.scrape_album(album_url, headers, album_name):
                            submit(track)
                    except Exception as error:
                        print(f"Album error {album_url}: {error}")
            except Exception as error:
                print(f"Album discovery error: {error}")
            drain_all(executor)
        self.signals.remove_loading_task.emit(game_id)
        suffix = f", skipped {rejected} unavailable" if rejected else ""
        self.signals.status_message.emit(f"Loaded {game_name} ({count} playable tracks{suffix})")

    def scrape_stream(self, page_url, link_pattern, headers):
        resp = requests.get(page_url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=lambda h: h and link_pattern in h):
            title = a.get_text(strip=True)
            if not title:
                continue
            row = a.find_parent('tr')
            artist = "Unknown Artist"
            if row:
                cell = row.find('td', class_='artist') or row.find('td', class_='artist_column')
                if cell:
                    artist = cell.get_text(strip=True)
                else:
                    span = row.find('span', class_='artist')
                    if span:
                        artist = span.get_text(strip=True)
                    else:
                        cells = row.find_all('td')
                        if len(cells) >= 2 and a in cells[0].find_all():
                            artist = cells[1].get_text(strip=True)
            full_url = urljoin(page_url, a['href'])
            track_type = 'remix' if '/remix/' in full_url else 'original'
            if artist == "Unknown Artist" and track_type == 'original':
                try:
                    detail = requests.get(full_url, headers=headers, timeout=5)
                    detail_soup = BeautifulSoup(detail.text, 'html.parser')
                    h2 = detail_soup.find('h2', string=re.compile(r'Music By', re.I))
                    if h2:
                        link = h2.find('a', class_='color-original')
                        if link:
                            artist = link.get_text(strip=True)
                    if artist == "Unknown Artist":
                        for elem in detail_soup.find_all(['h2', 'div', 'p']):
                            if re.search(r'Music By|Composed by|Written by', elem.get_text(), re.I):
                                link = elem.find('a', class_='color-original')
                                if link:
                                    artist = link.get_text(strip=True)
                                    break
                    if artist == "Unknown Artist":
                        meta = detail_soup.find('meta', attrs={'name': 'author'})
                        if meta and meta.get('content'):
                            artist = meta['content'].strip()
                except:
                    pass
            yield {
                'title': title,
                'artist': artist,
                'url': full_url,
                'preview_url': a.get('data-preview', '').strip(),
                'type': track_type,
                'duration': 0
            }

    def scrape_bandcamp(self, album_url, headers, album_name=None):
        opts = get_ydl_options()
        opts.update({'extract_flat': False, 'playlistend': 100})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(album_url, download=False)
                return [{
                    'title': entry.get('track') or entry.get('title') or 'Unknown Title',
                    'artist': entry.get('artist') or entry.get('uploader') or 'Unknown Artist',
                    'url': entry.get('webpage_url') or entry.get('url'),
                    'preview_url': entry.get('webpage_url') or entry.get('url'),
                    'type': 'album',
                    'album_name': album_name,
                    'duration': entry.get('duration', 0)
                } for entry in info.get('entries', []) if entry]
        except Exception as e:
            print(f"Bandcamp error: {e}")
            return []

    def scrape_album(self, album_url, headers, album_name=None):
        resp = requests.get(album_url, headers=headers, timeout=10)
        resp.raise_for_status()
        final = resp.url
        if 'bandcamp.com/album/' in final or ('bandcamp.com' in final and '/album' in final):
            return self.scrape_bandcamp(final, headers, album_name)
        soup = BeautifulSoup(resp.text, 'html.parser')
        if not album_name:
            title_tag = soup.find('title')
            album_name = title_tag.get_text(strip=True).split('|')[0].strip() if title_tag else "Unknown Album"
        table = soup.find('table', class_='tracklist')
        if table:
            tracks = []
            for row in table.find_all('tr'):
                if row.find('th'):
                    continue
                title_cell = row.find('td', class_='title') or row.find('td')
                if not title_cell:
                    continue
                title = title_cell.get_text(strip=True)
                if not title:
                    continue
                artist_cell = row.find('td', class_='artist')
                artist = artist_cell.get_text(strip=True) if artist_cell else "Unknown Artist"
                link = title_cell.find('a')
                track_url = urljoin(album_url, link['href']) if link else album_url
                play_btn = row.find('a', class_='play-button') or row.find('a', {'data-preview': True})
                preview = urljoin(album_url, play_btn['href']) if play_btn and play_btn.get('href') else ''
                tracks.append({
                    'title': title, 'artist': artist, 'url': track_url, 'preview_url': preview,
                    'type': 'album', 'album_name': album_name, 'duration': 0
                })
            return tracks
        playlist_url = None
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if 'soundcloud.com/player' in src:
                m = re.search(r'url=([^&]+)', src)
                if m:
                    playlist_url = unquote(m.group(1))
                    break
        if not playlist_url:
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'soundcloud.com/' in href and ('/sets/' in href or '/playlists/' in href):
                    playlist_url = href
                    break
        if playlist_url:
            tracks = get_playlist_tracks(playlist_url)
            for t in tracks:
                t['album_name'] = album_name
            return tracks
        return []

    # ---------- Loading indicators ----------
    def add_loading_task(self, game_id, name):
        self.loading_tasks[game_id] = {'name': name, 'count': 0}
        self.signals.loading_display_update.emit()

    def update_loading_count(self, game_id, count):
        if game_id in self.loading_tasks:
            self.loading_tasks[game_id]['count'] = count
            self.signals.loading_display_update.emit()

    def remove_loading_task(self, game_id):
        if game_id in self.loading_tasks:
            del self.loading_tasks[game_id]
            self.signals.loading_display_update.emit()

    def update_loading_display(self):
        if not self.loading_tasks:
            self.loading_label.setVisible(False)
            return
        self.loading_label.setVisible(True)
        self.loading_label.setText("\n".join(f"Loading {info['name']}: {info['count']} tracks" for info in self.loading_tasks.values()))

    def add_track_widget(self, game_id, track):
        widget = self.added_games[game_id].get('widget')
        if widget:
            widget.add_track(track)

    def show_browsable_tracks(self, game_id):
        self.clear_browser_tracks()
        for track in self.browsable_tracks.get(game_id, []):
            self.add_browser_track_widget(game_id, track)
        visible = sum(
            1 for track in self.browsable_tracks.get(game_id, [])
            if not (self.hide_originals and track.get('type') == 'original')
        )
        self.browser_summary.setText(f"{visible} visible tracks")

    def add_browser_track_widget(self, game_id, track):
        if game_id != self.selected_game_id:
            return
        if self.hide_originals and track.get('type') == 'original':
            self.browser_summary.setText("Original tracks hidden")
            return
        self.browser_empty_label.hide()
        widget = TrackWidget(track, game_id, self.browser_container, queue_item=False)
        self.browser_tracks_layout.addWidget(widget)
        self.browser_summary.setText(
            f"{len(self.browsable_tracks.get(game_id, []))} playable tracks"
        )

    def add_track_to_queue(self, game_id, track):
        if game_id not in self.added_games:
            info = self.game_catalog[game_id]
            self.added_games[game_id] = info
            self.tracks_by_game[game_id] = []
            widget = GameWidget(game_id, info, self.queue_container)
            self.queue_layout.insertWidget(max(0, self.queue_layout.count() - 1), widget)
            self.added_games[game_id]['widget'] = widget
        if any(item['url'] == track['url'] for item in self.tracks_by_game[game_id]):
            self.status_label.setText(f"Already queued: {track['title']}")
            return
        queued = dict(track)
        self.tracks_by_game[game_id].append(queued)
        self.master_tracklist = self.rebuild_master_list()
        self.add_track_widget(game_id, queued)
        self.status_label.setText(f"Queued: {track['title']}")
        self.signals.refresh_title.emit()

    # ---------- Playback ----------
    def play_track(self, track):
        if self.global_shuffle_active:
            self.stop_global_shuffle()
        self._play_track(track)

    def remove_track(self, game_id, track):
        if game_id in self.tracks_by_game:
            before = len(self.tracks_by_game[game_id])
            self.tracks_by_game[game_id] = [t for t in self.tracks_by_game[game_id] if t['url'] != track['url']]
            if len(self.tracks_by_game[game_id]) < before:
                self.master_tracklist = self.rebuild_master_list()
                widget = self.added_games[game_id].get('widget')
                if widget:
                    for w in widget.track_widgets:
                        if w.track == track:
                            w.deleteLater()
                            widget.track_widgets.remove(w)
                            break
                self.status_label.setText(f"Removed track: {track['title']}")
                if self.shuffle_active:
                    self.shuffle_queue = [t for t in self.shuffle_queue if t['url'] != track['url']]
                    if self.shuffle_index > len(self.shuffle_queue):
                        self.shuffle_index = len(self.shuffle_queue)
                if self.current_track and self.current_track['url'] == track['url']:
                    self.play_request_id += 1
                    self.player.stop()
                    self.current_track = None
                    self.play_button.setText("▶")
                    self.position_slider.setValue(0)
                self.signals.refresh_title.emit()

    def remove_game(self, game_id):
        if self.current_track and self.current_track.get('game_id') == game_id:
            self.play_request_id += 1
            self.player.stop()
            self.current_track = None
            self.play_button.setText("▶")
            self.position_slider.setValue(0)
        if game_id in self.added_games:
            widget = self.added_games[game_id].get('widget')
            if widget:
                widget.deleteLater()
            del self.added_games[game_id]
        if game_id in self.tracks_by_game:
            del self.tracks_by_game[game_id]
        self.master_tracklist = self.rebuild_master_list()
        if self.shuffle_active and not self.master_tracklist:
            self.stop_shuffle()
        self.remove_loading_task(game_id)
        self.signals.refresh_title.emit()

    def clear_queue(self):
        if not self.added_games:
            return
        if QMessageBox.question(self, "Clear Queue", "Remove all games and tracks from the queue?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            for gid in list(self.added_games.keys()):
                self.remove_game(gid)

    def rebuild_master_list(self):
        seen = set()
        result = []
        for tracks in self.tracks_by_game.values():
            for t in tracks:
                if t['url'] not in seen:
                    seen.add(t['url'])
                    result.append(t)
        return result

    def save_queue(self):
        if not self.added_games:
            QMessageBox.information(self, "Save Queue", "Nothing to save. Queue is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Queue", "", "JSON files (*.json)")
        if not path:
            return
        data = []
        for gid, info in self.added_games.items():
            serial = [{
                'title': t['title'], 'artist': t['artist'], 'url': t['url'],
                'preview_url': t.get('preview_url', ''), 'type': t.get('type', 'original'),
                'album_name': t.get('album_name'), 'duration': t.get('duration', 0),
                'game_name': t.get('game_name', info['name']), 'media_url': t.get('media_url', '')
            } for t in self.tracks_by_game.get(gid, [])]
            data.append({'game_id': gid, 'game_name': info['name'], 'game_url': info['url'],
                         'platform': info.get('platform', ''), 'tracks': serial})
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.last_queue_path = path
            self.settings.setValue("last_queue_path", path)
            self.status_label.setText(f"Queue saved to {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save queue: {e}")

    def load_queue(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Load Queue", "", "JSON files (*.json)")
            if not path:
                return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list) or any(
                not isinstance(game, dict)
                or not all(game.get(field) for field in ('game_id', 'game_name', 'game_url'))
                or not isinstance(game.get('tracks'), list)
                or not all(isinstance(track, dict) and track.get('title') and track.get('url') for track in game.get('tracks', []))
                for game in data
            ):
                raise ValueError("This is not a valid OC ReMix queue file.")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to read file: {e}")
            return
        for gid in list(self.added_games.keys()):
            self.remove_game(gid)
        self.tracks_by_game.clear()
        self.master_tracklist.clear()
        self.shuffle_queue.clear()
        self.shuffle_index = 0
        self.shuffle_active = False
        self.global_shuffle_active = False
        for game in data:
            gid = game['game_id']
            if gid not in self.game_catalog:
                self.game_catalog[gid] = {'name': game['game_name'], 'url': game['game_url'], 'platform': game.get('platform', '')}
            info = self.game_catalog[gid]
            self.added_games[gid] = info
            self.tracks_by_game[gid] = []
            widget = GameWidget(gid, info, self)
            self.queue_layout.addWidget(widget)
            self.added_games[gid]['widget'] = widget
            for tdata in game['tracks']:
                track = {
                    'title': tdata['title'], 'artist': tdata['artist'], 'url': tdata['url'],
                    'preview_url': tdata.get('preview_url', ''), 'type': tdata.get('type', 'original'),
                    'album_name': tdata.get('album_name'), 'duration': tdata.get('duration', 0),
                    'game_name': tdata.get('game_name', info['name']), 'game_id': gid,
                    'media_url': tdata.get('media_url', '')
                }
                self.tracks_by_game[gid].append(track)
                self.master_tracklist.append(track)
                widget.add_track(track)
        self.last_queue_path = path
        self.settings.setValue("last_queue_path", path)
        self.status_label.setText(f"Loaded queue from {os.path.basename(path)} ({len(self.master_tracklist)} tracks)")
        self.signals.refresh_title.emit()

    def _play_track(self, track):
        self.play_request_id += 1
        request_id = self.play_request_id
        self.player.stop()
        self.play_button.setText("…")
        self.play_button.setToolTip("Loading track…")
        self.status_label.setText(f"Loading: {track['title']}…")

        def worker():
            try:
                resolved_at = track.get('resolved_at', 0)
                if track.get('resolved_stream_url') and time.monotonic() - resolved_at < 300:
                    stream = track['resolved_stream_url']
                    duration = int(track.get('resolved_duration') or track.get('duration') or 0)
                    http_headers = track.get('resolved_headers', {})
                else:
                    stream, duration, http_headers = resolve_track_media(track, retry_count=3)
                if request_id != self.play_request_id:
                    return
                if duration > 0:
                    track['duration'] = duration
                self.signals.start_playback.emit(track, stream, duration, http_headers, request_id)
            except Exception as error:
                if request_id != self.play_request_id:
                    return
                print(f"Skipping {track['title']}: {error}")
                self.signals.status_message.emit(f"Could not play {track['title']}: {error}")
                self.signals.play_button_text.emit("▶")
                self.signals.track_playback_failed.emit(track)

        threading.Thread(target=worker, daemon=True).start()

    def handle_playback_failure(self, track):
        next_track = None
        if not (self.shuffle_active or self.global_shuffle_active) and self.master_tracklist:
            try:
                next_index = self.master_tracklist.index(track) + 1
                if next_index < len(self.master_tracklist):
                    next_track = self.master_tracklist[next_index]
            except ValueError:
                pass
        if self.auto_remove_invalid:
            self.remove_track(track.get('game_id', ''), track)
        if self.shuffle_active:
            self.play_next_in_queue()
        elif self.global_shuffle_active:
            self.global_shuffle_next()
        elif next_track:
            self._play_track(next_track)

    def start_playback(self, track, stream_url, duration, http_headers, request_id):
        if request_id != self.play_request_id:
            return
        try:
            self.player.play_stream(stream_url, duration, http_headers)
        except Exception as error:
            self.status_label.setText(f"Playback failed: {error}")
            self.play_button.setText("▶")
            self.play_button.setToolTip("Play or pause")
            self.handle_playback_failure(track)
            return
        self.current_track = track
        self.last_played_track = track
        self.playback_started_at = time.monotonic()
        self.play_history.append(track)
        if len(self.play_history) > 50:
            self.play_history.pop(0)
        game_name = track.get('game_name', '')
        artist = track['artist']
        if track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
            artist = artist[:11] + ' ' + artist[11:]
        self.status_label.setText(f"Now playing: {game_name} - {track['title']} — {artist}" if game_name else f"Now playing: {track['title']} — {artist}")
        self.tray_icon.setToolTip(self.status_label.text())
        self.play_button.setText("⏸")
        self.play_button.setToolTip("Pause")
        self.position_slider.setValue(0)
        self.signals.refresh_title.emit()
        QTimer.singleShot(1000, self.refresh_length)
        if self.auto_export_track and self.export_file_path:
            self.export_track_info(track)

    def refresh_length(self):
        if self.player.current_url and self.player.is_playing():
            length = self.player.get_length()
            if length > 0 and self.current_track:
                self.current_track['duration'] = length

    # ---------- Shuffle ----------
    def toggle_shuffle(self):
        if self.shuffle_active:
            self.stop_shuffle()
        else:
            self.start_shuffle()

    def start_shuffle(self):
        if not self.master_tracklist:
            self.status_label.setText("Add a game before starting shuffle.")
            self.shuffle_button.setChecked(False)
            return
        if self.global_shuffle_active:
            self.stop_global_shuffle()
        self.shuffle_active = True
        self.shuffle_button.setChecked(True)
        self.shuffle_button.setText("Stop shuffle")
        self.global_shuffle_button.setChecked(False)
        self.global_shuffle_button.setText("Global shuffle")
        tracks = self.master_tracklist.copy()
        random.shuffle(tracks)
        if self.last_played_track and len(tracks) > 1 and tracks[0] == self.last_played_track:
            for i in range(1, len(tracks)):
                if tracks[i] != self.last_played_track:
                    tracks[0], tracks[i] = tracks[i], tracks[0]
                    break
        self.shuffle_queue = tracks
        self.shuffle_index = 0
        self.play_next_in_queue()

    def stop_shuffle(self):
        self.shuffle_active = False
        self.play_request_id += 1
        self.shuffle_button.setChecked(False)
        self.shuffle_button.setText("Shuffle queue")
        self.player.stop()
        self.play_button.setText("▶")
        self.position_slider.setValue(0)
        self.status_label.setText("Shuffle stopped")

    def play_next_in_queue(self):
        if not self.shuffle_queue:
            self.stop_shuffle()
            return
        if self.shuffle_index >= len(self.shuffle_queue):
            if self.repeat_mode == 2:
                self.shuffle_index = 0
            elif self.repeat_mode == 3:
                self.stop_shuffle()
                return
            else:
                self.stop_shuffle()
                return
        next_track = self.shuffle_queue[self.shuffle_index]
        if len(self.shuffle_queue) > 1 and next_track == self.last_played_track:
            for offset in range(1, len(self.shuffle_queue)):
                cand = (self.shuffle_index + offset) % len(self.shuffle_queue)
                if self.shuffle_queue[cand] != self.last_played_track:
                    self.shuffle_queue[self.shuffle_index], self.shuffle_queue[cand] = self.shuffle_queue[cand], self.shuffle_queue[self.shuffle_index]
                    next_track = self.shuffle_queue[self.shuffle_index]
                    break
        self.shuffle_index += 1
        self._play_track(next_track)

    def on_next(self):
        if self.shuffle_active:
            self.play_next_in_queue()
        elif self.global_shuffle_active:
            self.global_shuffle_next()
        elif self.master_tracklist:
            try:
                idx = self.master_tracklist.index(self.current_track)
            except ValueError:
                idx = -1
            next_index = idx + 1
            if next_index >= len(self.master_tracklist):
                if self.repeat_mode == 2:
                    next_index = 0
                else:
                    return
            self._play_track(self.master_tracklist[next_index])

    def on_previous(self):
        if len(self.play_history) >= 2:
            self.play_history.pop()
            self._play_track(self.play_history[-1])
        elif not (self.shuffle_active or self.global_shuffle_active):
            try:
                idx = self.master_tracklist.index(self.current_track)
                if idx - 1 >= 0:
                    self._play_track(self.master_tracklist[idx - 1])
            except ValueError:
                pass

    # ---------- Global Shuffle ----------
    def toggle_global_shuffle(self):
        if self.global_shuffle_active:
            self.stop_global_shuffle()
        else:
            self.start_global_shuffle()

    def start_global_shuffle(self):
        if not self.game_catalog:
            self.global_shuffle_button.setChecked(False)
            self.status_label.setText("No games loaded. Please refresh first.")
            return
        if self.shuffle_active:
            self.stop_shuffle()
        self.global_shuffle_active = True
        self.global_shuffle_button.setChecked(True)
        self.global_shuffle_button.setText("Stop global")
        self.shuffle_button.setChecked(False)
        self.shuffle_button.setText("Shuffle queue")
        self.global_stop.clear()
        self.global_thread = threading.Thread(target=self.global_loop, daemon=True)
        self.global_thread.start()

    def stop_global_shuffle(self):
        self.global_shuffle_active = False
        self.play_request_id += 1
        self.global_stop.set()
        self.global_shuffle_button.setChecked(False)
        self.global_shuffle_button.setText("Global shuffle")
        self.player.stop()
        self.play_button.setText("▶")
        self.position_slider.setValue(0)
        self.status_label.setText("Global shuffle stopped")

    def global_loop(self):
        while self.global_shuffle_active and not self.global_stop.is_set():
            ids = list(self.game_catalog.keys())
            if not ids:
                time.sleep(0.5)
                continue
            gid = random.choice(ids)
            if gid not in self.global_track_cache:
                try:
                    tracks = self.fetch_global_tracks(self.game_catalog[gid]['url'])
                    self.global_track_cache[gid] = tracks
                except:
                    time.sleep(0.5)
                    continue
            tracks = self.global_track_cache[gid]
            if not tracks:
                time.sleep(0.5)
                continue
            track = random.choice(tracks)
            if self.last_played_track and len(tracks) > 1:
                while track == self.last_played_track:
                    track = random.choice(tracks)
            self.play_track_and_wait(track)
            if self.global_stop.is_set():
                break

    def fetch_global_tracks(self, game_url):
        headers = {"User-Agent": "OCReMixJukebox/1.0"}
        main = game_url.rstrip('/')
        entries = []
        try:
            entries.extend(self.scrape_stream(main, '/song/', headers))
        except: pass
        try:
            entries.extend(self.scrape_stream(main + '/remixes', '/remix/', headers))
        except: pass
        try:
            resp = requests.get(main, headers=headers, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = []
            for a in soup.find_all('a', href=lambda h: h and '/album/' in h):
                parent = a.find_parent('div', class_='widget-neutral')
                if parent and 'Promotion' in parent.get_text():
                    continue
                items.append((urljoin(main, a['href']), a.get_text(strip=True)))
            for idx, (url, name) in enumerate(items):
                if idx > 0:
                    time.sleep(2)
                try:
                    entries.extend(self.scrape_album(url, headers, name))
                except: pass
        except: pass
        seen = set()
        return [e for e in entries if e['url'] not in seen and not seen.add(e['url'])]

    def play_track_and_wait(self, track):
        self._play_track(track)
        for _ in range(100):
            if self.player.is_playing():
                break
            time.sleep(0.1)
        while self.global_shuffle_active and not self.global_stop.is_set():
            if not self.player.is_playing() and not self.player.paused:
                break
            time.sleep(0.2)
        if self.global_stop.is_set():
            self.player.stop()

    def global_shuffle_next(self):
        if self.global_shuffle_active:
            self.player.stop()

    # ---------- Repeat, Play/Pause, Volume, Seek ----------
    def handle_track_end(self):
        self.player.current_url = None
        self.player.paused = False
        if self.repeat_mode == 1 and self.current_track:
            self._play_track(self.current_track)
        elif self.shuffle_active:
            self.play_next_in_queue()
        elif self.global_shuffle_active:
            return
        elif self.repeat_mode in (2, 3):
            self.on_next()
            if self.play_button.text() != "…":
                self.play_button.setText("▶")
                self.position_slider.setValue(0)
        else:
            self.play_button.setText("▶")
            self.position_slider.setValue(0)
            self.elapsed_label.setText("0:00")

    def toggle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 4
        labels = {0: "🔁 Off", 1: "🔁 One", 2: "🔁 All", 3: "➡️ Queue"}
        descriptions = {
            0: "Repeat off: stop after the current track",
            1: "Repeat one: replay the current track",
            2: "Repeat all: loop the queue",
            3: "Queue: continue through tracks, then stop",
        }
        self.repeat_button.setText(labels[self.repeat_mode])
        self.repeat_button.setToolTip(descriptions[self.repeat_mode])

    def on_play_pause(self):
        if self.player.current_url is None:
            if self.play_button.text() == "…":
                self.play_request_id += 1
                self.play_button.setText("▶")
                self.status_label.setText("Loading cancelled")
            elif self.master_tracklist:
                self._play_track(self.master_tracklist[0])
            return
        self.player.pause()
        self.play_button.setText("▶" if self.player.paused else "⏸")
        self.play_button.setToolTip("Resume" if self.player.paused else "Pause")

    def change_volume(self, val):
        self.player.set_volume(val)
        self.settings.setValue("volume", val)

    def seek(self, value):
        if self.player.current_url:
            self.player.seek(value / 1000.0)

    def update_progress(self):
        if self.player.current_url:
            state = self.player.player.get_state()
            if state == vlc.State.Playing:
                self.playback_started_at = None
            elif state == vlc.State.Error:
                track = self.current_track
                self.player.stop()
                self.playback_started_at = None
                self.play_button.setText("▶")
                self.status_label.setText("VLC could not open the resolved stream URL.")
                if track:
                    self.handle_playback_failure(track)
                return
            elif state in (vlc.State.Opening, vlc.State.Buffering) and self.playback_started_at:
                waited = time.monotonic() - self.playback_started_at
                if waited > 15:
                    track = self.current_track
                    self.player.stop()
                    self.playback_started_at = None
                    self.play_button.setText("▶")
                    self.status_label.setText("Playback timed out while opening the stream.")
                    if track:
                        self.handle_playback_failure(track)
                    return
                self.status_label.setText(f"Opening stream… ({int(waited)}s)")
            pos = self.player.get_pos()
            length = self.player.get_length()
            if length > 0 and pos >= 0:
                if not self.position_slider.isSliderDown():
                    self.position_slider.setValue(int((pos / length) * 1000))
                cur = time.strftime('%M:%S', time.gmtime(pos))
                total = time.strftime('%M:%S', time.gmtime(length))
                self.elapsed_label.setText(cur)
                self.duration_label.setText(total)
                if self.current_track:
                    artist = self.current_track['artist']
                    if self.current_track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
                        artist = artist[:11] + ' ' + artist[11:]
                    game = self.current_track.get('game_name', '')
                    playback_state = "Paused: " if self.player.paused else "Now playing: "
                    self.status_label.setText(f"{playback_state}{game} - {self.current_track['title']} — {artist}  [{cur} / {total}]" if game else f"{playback_state}{self.current_track['title']} — {artist}  [{cur} / {total}]")
        if self.player.current_url and self.player.player.get_state() == vlc.State.Ended:
            self.handle_track_end()

    # ---------- Cookies ----------
    def load_cookies(self):
        global cookies_path
        decrypted = decrypt_cookies_auto()
        if decrypted:
            cookies_path = decrypted
            self.status_label.setText("SoundCloud cookies loaded (auto‑encrypted)")
            return
        plain = load_plain_cookies()
        if plain:
            cookies_path = plain
            self.status_label.setText("SoundCloud cookies loaded (plain text)")
            return
        if cookie_load_error:
            self.status_label.setText(cookie_load_error)
            return
        self.status_label.setText("No SoundCloud cookies found. Click Manage to add.")

    def manage_cookies(self):
        global cookies_path
        instructions = (
            "To fix SoundCloud 403/429 errors, you need a cookies file in Netscape format.\n\n"
            "1. Install a browser extension that exports cookies:\n"
            "   - Chrome/Edge: 'Get cookies.txt LOCALLY'\n"
            "   - Firefox: 'cookies.txt'\n\n"
            "2. Log into SoundCloud in your browser.\n\n"
            "3. Click the extension icon and choose 'Export' – save the file as 'soundcloud_cookies.txt'.\n\n"
            "4. Use the buttons below to load the file.\n\n"
            "• Auto‑Encrypt: uses your machine's unique ID (no password, automatically loaded on next startup).\n"
            "• Plain Text: readable by anyone with access to this folder."
        )
        if QMessageBox.question(self, "How to get SoundCloud cookies", instructions, QMessageBox.Ok|QMessageBox.Cancel) != QMessageBox.Ok:
            return
        filepath, _ = QFileDialog.getOpenFileName(self, "Select SoundCloud cookies.txt", "", "Text files (*.txt)")
        if not filepath:
            return
        if QMessageBox.question(self, "Storage Method", "Yes = Auto‑Encrypt (machine‑bound, no password)\nNo = Plain Text (no security)", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            if encrypt_cookies_auto(filepath):
                if QMessageBox.question(self, "Delete Original?", "Delete the plaintext file?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
                    try: os.remove(filepath)
                    except: pass
                decrypted = decrypt_cookies_auto()
                if decrypted:
                    cookies_path = decrypted
                    self.status_label.setText("Cookies auto‑encrypted and loaded")
                else:
                    self.status_label.setText("Encryption succeeded but failed to decrypt")
            else:
                self.status_label.setText("Encryption failed")
        else:
            if save_plain_cookies(filepath):
                cookies_path = plain_cookies_file
                self.status_label.setText("Cookies saved as plain text")
            else:
                self.status_label.setText("Failed to save plain cookies")

    # ---------- Tray and Media Keys ----------
    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        pixmap = QPixmap(64,64)
        pixmap.fill(Qt.black)
        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip("OC ReMix Jukebox")
        self.tray_icon.activated.connect(self.on_tray_click)
        menu = QMenu()
        menu.addAction("Show", self.show_app)
        menu.addAction("Hide", self.hide_app)
        menu.addAction("Quit", self.quit_app)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_window()

    def toggle_window(self):
        self.show_app() if not self.isVisible() else self.hide_app()

    def show_app(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def hide_app(self):
        self.hide()
        self.tray_icon.showMessage("OC ReMix Jukebox", "Running in system tray", QSystemTrayIcon.Information, 1000)

    def quit_app(self):
        self.player.stop()
        self.global_shuffle_active = False
        self.global_stop.set()
        if self.global_thread and self.global_thread.is_alive():
            self.global_thread.join(timeout=1)
        if hasattr(self, 'listener') and self.listener:
            self.listener.stop()
        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide()
        if self.progress_timer:
            self.progress_timer.stop()
        QApplication.instance().quit()

    def closeEvent(self, event):
        event.accept()
        QTimer.singleShot(0, self.quit_app)

    def changeEvent(self, event):
        if event.type() == event.WindowStateChange and self.isMinimized():
            self.hide_app()
            event.ignore()

    def setup_media_keys(self):
        try:
            if pynput_keyboard is None:
                raise RuntimeError("pynput is unavailable (no supported keyboard backend)")
            self.listener = pynput_keyboard.Listener(on_press=self.on_key_press)
            self.listener.daemon = True
            self.listener.start()
            self.status_label.setText("Media keys registered")
        except Exception as e:
            self.status_label.setText(f"Media keys failed: {e}")

    def on_key_press(self, key):
        try:
            key_name = getattr(key, 'name', '')
            if key_name in ('media_play_pause', 'play_pause'):
                self.signals.media_play_pause.emit()
            elif key_name in ('media_previous', 'previous'):
                self.signals.media_previous.emit()
            elif key_name in ('media_next', 'next'):
                self.signals.media_next.emit()
                return
            if hasattr(key, 'vk') and key.vk:
                if key.vk == 0xB3:
                    self.signals.media_play_pause.emit()
                elif key.vk == 0xB0:
                    self.signals.media_previous.emit()
                elif key.vk == 0xB1:
                    self.signals.media_next.emit()
            elif hasattr(key, 'name'):
                if key.name == 'media_play_pause':
                    self.signals.media_play_pause.emit()
                elif key.name == 'media_previous':
                    self.signals.media_previous.emit()
                elif key.name == 'media_next':
                    self.signals.media_next.emit()
        except:
            pass

    def on_status_click(self, event):
        if self.current_track:
            QDesktopServices.openUrl(QUrl(self.current_track['url']))

def main():
    app = QApplication(sys.argv)
    apply_system_theme(app)
    app.setQuitOnLastWindowClosed(False)
    try:
        window = OCRemixWindow()
    except AudioInitializationError as error:
        QMessageBox.critical(None, "Audio Initialization Error", str(error))
        return 1
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())