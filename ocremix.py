#!/usr/bin/env python3
import sys, os, re, json, time, threading, random, tempfile, base64, uuid, shutil
from urllib.parse import urljoin, urlencode, unquote
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLineEdit, QPushButton, QLabel, QSlider, QScrollArea, QListWidget,
    QMessageBox, QFileDialog, QSystemTrayIcon, QMenu, QAction, QFrame, QStyleFactory)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QUrl
from PyQt5.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices
from pynput import keyboard as pynput_keyboard
import requests, yt_dlp, vlc

os.environ['VLC_VERBOSE'] = '-1'

soundcloud_cache = {}
cookies_path = None
encrypted_cookies_file = "soundcloud_cookies.enc"
plain_cookies_file = "soundcloud_cookies_plain.txt"
temp_cookies_file = None

# ---------- System theme ----------
def is_windows_dark_mode():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except:
        return False

def apply_system_theme(app):
    if is_windows_dark_mode():
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
    mac = uuid.getnode()
    if mac in (0xffffffffffff, 0):
        key_file = "machine_key.bin"
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
    try:
        if not os.path.exists(encrypted_cookies_file):
            return None
        with open(encrypted_cookies_file, 'rb') as f:
            encrypted = f.read()
        plain = Fernet(get_machine_key()).decrypt(encrypted)
        global temp_cookies_file
        temp_cookies_file = tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt')
        temp_cookies_file.write(plain)
        temp_cookies_file.close()
        return temp_cookies_file.name
    except Exception as e:
        print(f"Auto decryption error: {e}")
        return None

def save_plain_cookies(source_path):
    try:
        shutil.copy2(source_path, plain_cookies_file)
        return True
    except Exception as e:
        QMessageBox.critical(None, "Error", f"Failed to save plain cookies: {e}")
        return False

def load_plain_cookies():
    return plain_cookies_file if os.path.exists(plain_cookies_file) else None

# ---------- Audio player ----------
class AudioPlayer:
    def __init__(self):
        self.instance = vlc.Instance('--quiet', '--no-xlib', '--log-verbose=0', '--verbose=-1')
        self.player = self.instance.media_player_new()
        self.current_url = None
        self.length = 0
        self.paused = False

    def play_stream(self, url, duration=None):
        self.stop()
        self.current_url = url
        self.length = duration or 0
        media = self.instance.media_new(url)
        self.player.set_media(media)
        self.player.play()
        self.paused = False

    def pause(self):
        if self.player.get_state() == vlc.State.Playing:
            self.player.pause()
            self.paused = True
        elif self.paused:
            self.player.play()
            self.paused = False

    def stop(self):
        self.player.stop()
        self.current_url = None
        self.paused = False
        self.length = 0

    def set_volume(self, vol):
        self.player.audio_set_volume(int(vol))

    def get_pos(self):
        return self.player.get_time() / 1000.0 if self.player.is_playing() else -1

    def get_length(self):
        return self.player.get_length() / 1000.0

    def is_playing(self):
        return self.player.is_playing()

    def seek(self, fraction):
        length_ms = self.player.get_length()
        if length_ms > 0:
            self.player.set_time(int(fraction * length_ms))

# ---------- YouTube-DL helpers ----------
def get_ydl_options():
    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 5,
        'retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://soundcloud.com/',
        }
    }
    if cookies_path and os.path.exists(cookies_path):
        opts['cookiefile'] = cookies_path
    return opts

def get_stream_url(media_url, retry_count=5):
    for attempt in range(retry_count):
        try:
            with yt_dlp.YoutubeDL(get_ydl_options()) as ydl:
                info = ydl.extract_info(media_url, download=False)
                duration = info.get('duration', 0)
                formats = info.get('formats', [])
                audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                stream_url = max(audio_formats, key=lambda f: f.get('abr',0) or 0)['url'] if audio_formats else info['url']
                return stream_url, duration
        except Exception as e:
            print(f"Stream error (attempt {attempt+1}): {e}")
            if '429' in str(e):
                time.sleep(3 * (2 ** attempt))
            else:
                break
    raise Exception("Failed to extract stream after retries")

def get_playlist_tracks(playlist_url, retry=5):
    if playlist_url in soundcloud_cache:
        return soundcloud_cache[playlist_url]
    opts = get_ydl_options()
    opts.update({'extract_flat': False, 'playlistend': 100})
    for attempt in range(retry):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                tracks = []
                for entry in info.get('entries', []):
                    if not entry: continue
                    track_url = entry.get('webpage_url') or entry.get('url')
                    if track_url:
                        tracks.append({
                            'title': entry.get('track') or entry.get('title') or 'Unknown Title',
                            'artist': entry.get('artist') or entry.get('uploader') or entry.get('creator') or 'Unknown Artist',
                            'url': track_url,
                            'preview_url': track_url,
                            'type': 'album',
                            'album_name': None,
                            'duration': entry.get('duration', 0)
                        })
            soundcloud_cache[playlist_url] = tracks
            return tracks
        except Exception as e:
            print(f"Playlist error (attempt {attempt+1}): {e}")
            if '429' in str(e):
                time.sleep(3 * (2 ** attempt))
            else:
                break
    return []

def find_media_url(page_url):
    headers = {"User-Agent": "OCReMixJukebox/1.0"}
    try:
        resp = requests.get(page_url, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception:
        return None
    soup = BeautifulSoup(resp.text, 'html.parser')
    direct = soup.find('a', href=re.compile(r'\.mp3$', re.I))
    if direct:
        return urljoin(page_url, direct['href'])
    for iframe in soup.find_all('iframe', src=True):
        src = iframe['src']
        if 'youtube.com/embed/' in src or 'youtu.be/' in src:
            return f"https://www.youtube.com/watch?v={src.split('/')[-1].split('?')[0]}"
        if 'soundcloud.com/' in src:
            return src
    preview = soup.find('a', {'data-preview': True})
    if preview and preview.get('data-preview'):
        return preview['data-preview']
    audio = soup.find('audio')
    if audio:
        src = audio.get('src') or (audio.find('source') and audio.find('source').get('src'))
        if src:
            return urljoin(page_url, src)
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'ocremix.org' not in href:
            return href
    return None

def is_soundcloud_profile(url):
    if 'soundcloud.com/' in url:
        parts = url.split('soundcloud.com/')[-1].strip('/').split('/')
        return len(parts) == 1 and parts[0] and not parts[0].startswith('?')
    return False

# ---------- UI Widgets ----------
class TrackWidget(QFrame):
    def __init__(self, track, game_id, parent=None):
        super().__init__(parent)
        self.track = track
        self.game_id = game_id
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(30)
        self.play_button.clicked.connect(lambda: self.window().play_track(self.track))
        layout.addWidget(self.play_button)

        artist = self.track['artist']
        if self.track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
            artist = artist[:11] + ' ' + artist[11:]
        track_type = self.track.get('type', 'original')
        prefix = {
            'album': f"[A={self.track.get('album_name','').replace('Album: ','')}] " if self.track.get('album_name') else "[A] ",
            'remix': "[R] ",
            'original': "[O] "
        }.get(track_type, "[O] ")
        display = f"{prefix}{self.track['title']} — {artist}"
        self.label = QLabel(display)
        self.label.setWordWrap(True)
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.mousePressEvent = lambda e: QDesktopServices.openUrl(QUrl(self.track['url']))
        layout.addWidget(self.label, 1)

        self.remove_button = QPushButton("✕")
        self.remove_button.setFixedWidth(25)
        self.remove_button.setStyleSheet("background-color: red;")
        self.remove_button.clicked.connect(lambda: self.window().remove_track(self.game_id, self.track))
        layout.addWidget(self.remove_button)

class GameWidget(QFrame):
    def __init__(self, game_id, game_info, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_info = game_info
        self.track_widgets = []
        self.collapsed = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)

        self.header = QFrame()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(5,5,5,5)
        self.arrow_button = QPushButton("▼")
        self.arrow_button.setFixedWidth(30)
        self.arrow_button.setFlat(True)
        self.arrow_button.clicked.connect(self.toggle_collapse)
        header_layout.addWidget(self.arrow_button)

        self.title_label = QLabel(self.game_info['name'])
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        self.title_label.setCursor(Qt.PointingHandCursor)
        self.title_label.mousePressEvent = lambda e: self.toggle_collapse()
        header_layout.addWidget(self.title_label, 1)

        self.remove_button = QPushButton("✕")
        self.remove_button.setFixedWidth(30)
        self.remove_button.clicked.connect(lambda: self.window().remove_game(self.game_id))
        header_layout.addWidget(self.remove_button)
        layout.addWidget(self.header)

        self.track_container = QWidget()
        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setContentsMargins(0,0,0,0)
        self.track_layout.setSpacing(1)
        layout.addWidget(self.track_container)

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.track_container.setVisible(not self.collapsed)
        self.arrow_button.setText("▶" if self.collapsed else "▼")

    def add_track(self, track):
        widget = TrackWidget(track, self.game_id, self.parentWidget())
        self.track_layout.addWidget(widget)
        self.track_widgets.append(widget)

    def clear_tracks(self):
        for w in self.track_widgets:
            w.deleteLater()
        self.track_widgets.clear()

# ---------- Signals ----------
class UpdateSignals(QObject):
    append_track = pyqtSignal(str, dict)
    update_loading_count = pyqtSignal(str, int)
    remove_loading_task = pyqtSignal(str)
    status_message = pyqtSignal(str)
    play_button_text = pyqtSignal(str)
    start_playback = pyqtSignal(dict, str, int)
    loading_display_update = pyqtSignal()
    skip_to_next = pyqtSignal()
    skip_global_shuffle_next = pyqtSignal()
    media_play_pause = pyqtSignal()
    media_next = pyqtSignal()
    media_previous = pyqtSignal()
    remove_invalid_track = pyqtSignal(str, dict)
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
        self.cache_file = "games_cache.json"
        self.added_games = {}
        self.tracks_by_game = {}
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
        self.loading_tasks = {}
        self.signals = UpdateSignals()
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress)
        self.progress_timer.start(500)
        self.global_stop = threading.Event()
        self.global_thread = None
        self.settings = QSettings("OCRemix", "Jukebox")
        self.auto_remove_invalid = self.settings.value("auto_remove_invalid", False, type=bool)
        self.auto_export_track = False
        self.export_file_path = None
        self.last_queue_path = self.settings.value("last_queue_path", "", type=str)

        self.setup_ui()
        self.signals.append_track.connect(self.add_track_widget)
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
        left_layout.setContentsMargins(0,0,0,0)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search games...")
        self.search_box.textChanged.connect(self.filter_games)
        left_layout.addWidget(self.search_box)
        self.game_list = QListWidget()
        self.game_list.itemClicked.connect(self.on_game_click)
        left_layout.addWidget(self.game_list)

        # right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_container = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_container)
        self.queue_layout.setAlignment(Qt.AlignTop)
        self.queue_scroll.setWidget(self.queue_container)
        right_layout.addWidget(self.queue_scroll)

        self.loading_label = QLabel()
        self.loading_label.setVisible(False)
        right_layout.addWidget(self.loading_label)

        # controls
        ctrl = QHBoxLayout()
        self.shuffle_button = QPushButton("Shuffle All")
        self.shuffle_button.clicked.connect(self.toggle_shuffle)
        ctrl.addWidget(self.shuffle_button)
        self.global_shuffle_button = QPushButton("Global Shuffle")
        self.global_shuffle_button.clicked.connect(self.toggle_global_shuffle)
        ctrl.addWidget(self.global_shuffle_button)
        self.prev_button = QPushButton("⏮")
        self.prev_button.clicked.connect(self.on_previous)
        ctrl.addWidget(self.prev_button)
        self.play_button = QPushButton("▶")
        self.play_button.clicked.connect(self.on_play_pause)
        ctrl.addWidget(self.play_button)
        self.next_button = QPushButton("⏭")
        self.next_button.clicked.connect(self.on_next)
        ctrl.addWidget(self.next_button)
        self.repeat_button = QPushButton("🔁 Off")
        self.repeat_button.clicked.connect(self.toggle_repeat)
        ctrl.addWidget(self.repeat_button)
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.sliderMoved.connect(self.seek)
        ctrl.addWidget(self.position_slider, 1)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self.change_volume)
        ctrl.addWidget(self.volume_slider)
        right_layout.addLayout(ctrl)

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

    # ---------- Settings ----------
    def toggle_auto_remove(self, checked):
        self.auto_remove_invalid = checked
        self.settings.setValue("auto_remove_invalid", checked)

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
        if game_id in self.added_games:
            self.status_label.setText("Game already in queue")
            return
        info = self.game_catalog[game_id]
        self.added_games[game_id] = info
        self.tracks_by_game[game_id] = []
        widget = GameWidget(game_id, info, self)
        self.queue_layout.addWidget(widget)
        self.added_games[game_id]['widget'] = widget
        self.add_loading_task(game_id, info['name'])
        self.status_label.setText(f"Fetching tracks for {info['name']}...")
        threading.Thread(target=self.load_tracks_incremental, args=(game_id,), daemon=True).start()
        self.signals.refresh_title.emit()

    def load_tracks_incremental(self, game_id):
        url = self.game_catalog[game_id]['url']
        headers = {"User-Agent": "OCReMixJukebox/1.0"}
        main = url.rstrip('/')
        count = 0
        game_name = self.game_catalog[game_id]['name']

        def process(track):
            nonlocal count
            track.update({'game_id': game_id, 'game_name': game_name, 'duration': track.get('duration', 0)})
            self.tracks_by_game[game_id].append(track)
            self.master_tracklist.append(track)
            count += 1
            self.signals.append_track.emit(game_id, track)
            self.signals.update_loading_count.emit(game_id, count)
            self.signals.refresh_title.emit()

        try:
            for t in self.scrape_stream(main, '/song/', headers):
                process(t)
        except Exception as e:
            print(f"Originals error: {e}")
        try:
            for t in self.scrape_stream(main + '/remixes', '/remix/', headers):
                process(t)
        except Exception as e:
            print(f"Remixes error: {e}")
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
            for idx, (album_url, album_name) in enumerate(items):
                if idx > 0:
                    time.sleep(2)
                try:
                    for t in self.scrape_album(album_url, headers, album_name):
                        process(t)
                except Exception as e:
                    print(f"Album error {album_url}: {e}")
        except Exception as e:
            print(f"Album discovery error: {e}")
        self.signals.remove_loading_task.emit(game_id)
        self.signals.status_message.emit(f"Added {game_name} ({count} tracks)")

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
                    self.player.stop()
                    self.play_button.setText("▶")
                    self.position_slider.setValue(0)
                    self.handle_track_end()
                self.signals.refresh_title.emit()

    def remove_game(self, game_id):
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
                'game_name': t.get('game_name', info['name'])
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
                    'game_name': tdata.get('game_name', info['name']), 'game_id': gid
                }
                self.tracks_by_game[gid].append(track)
                self.master_tracklist.append(track)
                widget.add_track(track)
        self.last_queue_path = path
        self.settings.setValue("last_queue_path", path)
        self.status_label.setText(f"Loaded queue from {os.path.basename(path)} ({len(self.master_tracklist)} tracks)")
        self.signals.refresh_title.emit()

    def _play_track(self, track):
        self.signals.status_message.emit(f"Loading: {track['title']}...")
        self.signals.play_button_text.emit("⏸")
        def worker():
            self.player.stop()
            self.current_track = track
            self.last_played_track = track
            game_name = track.get('game_name', '')
            artist = track.get('artist', '')
            self.signals.update_tray_tooltip.emit(f"{game_name} - {track['title']} — {artist}" if game_name else f"{track['title']} — {artist}")
            try:
                preview = track.get('preview_url', '')
                media_url = preview if (preview and not is_soundcloud_profile(preview)) else find_media_url(track['url'])
                if not media_url or media_url == '/' or not media_url.startswith(('http://','https://')):
                    raise Exception("No valid media URL")
                stream, duration = get_stream_url(media_url)
                if duration > 0:
                    track['duration'] = duration
                self.signals.start_playback.emit(track, stream, duration)
            except Exception as e:
                print(f"Skipping {track['title']}: {e}")
                self.signals.status_message.emit(f"⏭️ Skipping: {track['title']}")
                self.signals.play_button_text.emit("▶")
                if self.auto_remove_invalid:
                    self.signals.remove_invalid_track.emit(track.get('game_id', ''), track)
                if self.shuffle_active:
                    self.signals.skip_to_next.emit()
                elif self.global_shuffle_active:
                    self.signals.skip_global_shuffle_next.emit()
                else:
                    try:
                        idx = self.master_tracklist.index(self.current_track)
                        if idx + 1 < len(self.master_tracklist):
                            self._play_track(self.master_tracklist[idx + 1])
                    except ValueError:
                        pass
        threading.Thread(target=worker, daemon=True).start()

    def start_playback(self, track, stream_url, duration):
        self.player.play_stream(stream_url, duration)
        self.play_history.append(track)
        if len(self.play_history) > 50:
            self.play_history.pop(0)
        game_name = track.get('game_name', '')
        artist = track['artist']
        if track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
            artist = artist[:11] + ' ' + artist[11:]
        self.status_label.setText(f"Now playing: {game_name} - {track['title']} — {artist}" if game_name else f"Now playing: {track['title']} — {artist}")
        self.play_button.setText("⏸")
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
            self.status_label.setText("Add some games first!")
            return
        if self.global_shuffle_active:
            self.stop_global_shuffle()
        self.shuffle_active = True
        self.shuffle_button.setText("Stop Shuffle")
        self.global_shuffle_button.setText("Global Shuffle")
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
        self.shuffle_button.setText("Shuffle All")
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
        else:
            try:
                idx = self.master_tracklist.index(self.current_track)
                if idx + 1 < len(self.master_tracklist):
                    self._play_track(self.master_tracklist[idx + 1])
            except ValueError:
                pass

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
            self.status_label.setText("No games loaded. Please refresh first.")
            return
        if self.shuffle_active:
            self.stop_shuffle()
        self.global_shuffle_active = True
        self.global_shuffle_button.setText("Stop Global")
        self.shuffle_button.setText("Shuffle All")
        self.global_stop.clear()
        self.global_thread = threading.Thread(target=self.global_loop, daemon=True)
        self.global_thread.start()

    def stop_global_shuffle(self):
        self.global_shuffle_active = False
        self.global_stop.set()
        self.global_shuffle_button.setText("Global Shuffle")
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
        if self.repeat_mode == 1:
            if self.current_track:
                self._play_track(self.current_track)
            return
        if self.repeat_mode == 2:
            if self.shuffle_active:
                self.play_next_in_queue()
            return
        if self.repeat_mode == 3:
            if self.shuffle_active:
                self.play_next_in_queue()
            else:
                self.player.stop()
                self.play_button.setText("▶")
                self.position_slider.setValue(0)
            return
        if self.shuffle_active:
            self.play_next_in_queue()
        elif not self.global_shuffle_active:
            self.player.stop()
            self.play_button.setText("▶")
            self.position_slider.setValue(0)

    def toggle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 4
        labels = {0: "🔁 Off", 1: "🔁 One", 2: "🔁 All", 3: "▶️ Sequential"}
        self.repeat_button.setText(labels[self.repeat_mode])

    def on_play_pause(self):
        if self.player.current_url is None:
            return
        self.player.pause()
        self.play_button.setText("▶" if self.player.paused else "⏸")

    def change_volume(self, val):
        self.player.set_volume(val)

    def seek(self, value):
        if self.player.current_url:
            self.player.seek(value / 1000.0)

    def update_progress(self):
        if self.player.is_playing():
            pos = self.player.get_pos()
            length = self.player.get_length()
            if length == 0 and self.current_track and self.current_track.get('duration', 0) > 0:
                length = self.current_track['duration']
            if length > 0 and pos >= 0:
                self.position_slider.setValue(int((pos / length) * 1000))
                cur = time.strftime('%M:%S', time.gmtime(pos))
                total = time.strftime('%M:%S', time.gmtime(length))
                if self.current_track:
                    artist = self.current_track['artist']
                    if self.current_track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
                        artist = artist[:11] + ' ' + artist[11:]
                    game = self.current_track.get('game_name', '')
                    self.status_label.setText(f"Now playing: {game} - {self.current_track['title']} — {artist}  [{cur} / {total}]" if game else f"Now playing: {self.current_track['title']} — {artist}  [{cur} / {total}]")
        if self.player.current_url and not self.player.is_playing() and not self.player.paused:
            if self.player.player.get_state() == vlc.State.Ended:
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
        QApplication.quit()
        sys.exit(0)

    def closeEvent(self, event):
        self.quit_app()
        event.accept()

    def changeEvent(self, event):
        if event.type() == event.WindowStateChange and self.isMinimized():
            self.hide_app()
            event.ignore()

    def setup_media_keys(self):
        try:
            self.listener = pynput_keyboard.Listener(on_press=self.on_key_press)
            self.listener.daemon = True
            self.listener.start()
            self.status_label.setText("Media keys registered")
        except Exception as e:
            self.status_label.setText(f"Media keys failed: {e}")

    def on_key_press(self, key):
        try:
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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_system_theme(app)
    app.setQuitOnLastWindowClosed(False)
    window = OCRemixWindow()
    window.show()
    sys.exit(app.exec_())