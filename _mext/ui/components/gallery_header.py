"""Gallery-style top navigation bar.

Replaces the Pivot navigation with a compact header containing
segmented tabs, search, and user menu.
"""

from __future__ import annotations

from typing import Optional

from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    SearchLineEdit,
    SegmentedWidget,
    ToolButton,
)
from PyQt6.QtCore import Qt, pyqtSignal as Signal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from _mext.ui.styles import (
    COLOR_BG_ELEVATED,
    COLOR_BORDER,
    COLOR_TEXT_SECONDARY,
    SPACING_LG,
    SPACING_MD,
    apply_themed_style,
)


class GalleryHeaderBar(QWidget):
    """Gallery-style top navigation bar.

    Provides segmented content tabs (Discover / Featured / Trending / Newest),
    a search field, and a user menu button.

    Signals
    -------
    tab_changed(str)
        Emitted when user switches tab: "discover", "featured", "trending", "newest".
    search_triggered(str)
        Emitted (after debounce) when the search query changes.
    user_menu_requested()
        Emitted when user clicks the user/avatar button.
    """

    tab_changed = Signal(str)
    search_triggered = Signal(str)
    user_menu_requested = Signal()

    _TABS = [
        ("discover", "\u53d1\u73b0"),      # 发现
        ("featured", "\u7cbe\u9009"),       # 精选
        ("trending", "\u70ed\u95e8"),       # 热门
        ("newest", "\u65b0\u54c1"),         # 新品
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("GalleryHeaderBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(56)
        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING_LG, 6, SPACING_LG, 6)
        layout.setSpacing(SPACING_MD)

        self._title = CaptionLabel("素材广场", self)
        self._title.setObjectName("GalleryHeaderTitle")
        layout.addWidget(self._title)

        # Left: segmented tabs
        self._segment = SegmentedWidget(self)
        for key, label in self._TABS:
            self._segment.addItem(routeKey=key, text=label)
        self._segment.setCurrentItem("discover")
        layout.addWidget(self._segment)

        layout.addStretch(1)

        # Center: search
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText("\u641c\u7d22\u7d20\u6750...")  # 搜索素材...
        self._search.setMinimumWidth(220)
        self._search.setMaximumWidth(360)
        self._search.setFixedHeight(36)
        self._search.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search.setClearButtonEnabled(True)
        layout.addWidget(self._search, stretch=1)

        # Right: user button
        self._user_btn = ToolButton(FluentIcon.PEOPLE, self)
        self._user_btn.setFixedSize(36, 36)
        self._user_btn.setToolTip("账户与下载")
        layout.addWidget(self._user_btn)

    def _connect_signals(self) -> None:
        self._segment.currentItemChanged.connect(self._on_tab_changed)
        self._search.searchSignal.connect(self._on_search)
        self._search.clearSignal.connect(lambda: self.search_triggered.emit(""))
        self._user_btn.clicked.connect(self.user_menu_requested.emit)

    def _on_tab_changed(self, route_key: str) -> None:
        self.tab_changed.emit(route_key)

    def _on_search(self, text: str) -> None:
        self.search_triggered.emit(text.strip())

    # ── Public API ────────────────────────────────────────────

    def set_current_tab(self, key: str) -> None:
        """Programmatically switch the active tab."""
        self._segment.setCurrentItem(key)

    @property
    def search_text(self) -> str:
        return self._search.text().strip()

    @search_text.setter
    def search_text(self, value: str) -> None:
        self._search.setText(value)

    def _apply_styles(self) -> None:
        light_qss = f"""
        QWidget#GalleryHeaderBar {{
            background-color: {COLOR_BG_ELEVATED[0]};
            border-bottom: 1px solid {COLOR_BORDER[0]};
        }}
        CaptionLabel#GalleryHeaderTitle {{
            color: {COLOR_TEXT_SECONDARY[0]};
            font-weight: 600;
        }}
        """
        dark_qss = f"""
        QWidget#GalleryHeaderBar {{
            background-color: {COLOR_BG_ELEVATED[1]};
            border-bottom: 1px solid {COLOR_BORDER[1]};
        }}
        CaptionLabel#GalleryHeaderTitle {{
            color: {COLOR_TEXT_SECONDARY[1]};
            font-weight: 600;
        }}
        """
        apply_themed_style(self, light_qss, dark_qss)
