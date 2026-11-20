from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class TrackWidget(QFrame):
    def __init__(self, track, game_id, parent=None, queue_item=True):
        super().__init__(parent)
        self.track = track
        self.game_id = game_id
        self.queue_item = queue_item
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(32)
        self.play_button.setToolTip(f"Play {self.track['title']}")
        self.play_button.clicked.connect(lambda: self.window().play_track(self.track))
        layout.addWidget(self.play_button)

        artist = self.track['artist']
        if self.track.get('type') == 'remix' and artist.lower().startswith('arranged by') and artist[11:12] != ' ':
            artist = artist[:11] + ' ' + artist[11:]
        track_type = self.track.get('type', 'original')
        prefix = {
            'album': f"[A={self.track.get('album_name','').replace('Album: ','')}] " if self.track.get('album_name') else "[A] ",
            'remix': "[R] ",
            'original': "[O] ",
        }.get(track_type, "[O] ")
        display = f"{prefix}{self.track['title']} — {artist}"
        self.label = QLabel(display)
        self.label.setWordWrap(True)
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.mousePressEvent = self.open_track_page
        layout.addWidget(self.label, 1)

        self.action_button = QPushButton("✕" if self.queue_item else "+")
        self.action_button.setFixedWidth(28)
        self.action_button.setToolTip("Remove from queue" if self.queue_item else "Add to queue")
        if self.queue_item:
            self.action_button.clicked.connect(lambda: self.window().remove_track(self.game_id, self.track))
        else:
            self.action_button.clicked.connect(lambda: self.window().add_track_to_queue(self.game_id, self.track))
        layout.addWidget(self.action_button)

    def open_track_page(self, event):
        QDesktopServices.openUrl(QUrl(self.track['url']))
        event.accept()


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
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)
        self.setFrameShape(QFrame.StyledPanel)

        self.header = QFrame()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(5, 5, 5, 5)
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
        self.title_label.mousePressEvent = self.handle_header_click
        header_layout.addWidget(self.title_label, 1)

        self.remove_button = QPushButton("✕")
        self.remove_button.setFixedWidth(30)
        self.remove_button.clicked.connect(lambda: self.window().remove_game(self.game_id))
        header_layout.addWidget(self.remove_button)
        layout.addWidget(self.header)

        self.track_container = QWidget()
        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setContentsMargins(0, 0, 0, 0)
        self.track_layout.setSpacing(1)
        layout.addWidget(self.track_container)

    def handle_header_click(self, event):
        self.toggle_collapse()
        event.accept()

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.track_container.setVisible(not self.collapsed)
        self.arrow_button.setText("▶" if self.collapsed else "▼")

    def add_track(self, track):
        widget = TrackWidget(track, self.game_id, self.parentWidget())
        self.track_layout.addWidget(widget)
        self.track_widgets.append(widget)

    def clear_tracks(self):
        for widget in self.track_widgets:
            widget.deleteLater()
        self.track_widgets.clear()
