"""_mext 扩展模块专用样式常量。"""

from __future__ import annotations

from typing import Any

from qfluentwidgets import isDarkTheme, setCustomStyleSheet

COLOR_TEXT_PRIMARY = ("#333333", "#eeeeee")
COLOR_TEXT_SECONDARY = ("#666666", "#aaaaaa")
COLOR_TEXT_MUTED = ("#999999", "#777777")
COLOR_BG_SURFACE = ("#f8f9fa", "#2d2d2d")
COLOR_BG_INSET = ("#ffffff", "#1e1e1e")
COLOR_BG_ELEVATED = ("#ffffff", "#333333")
COLOR_BORDER = ("#dddddd", "#555555")
COLOR_SUCCESS = ("#4CAF50", "#66BB6A")
COLOR_ERROR = ("#dc3545", "#e74c3c")
COLOR_WARNING = ("#ff9800", "#ffa726")
COLOR_ACCENT = ("#ff6b8b", "#ff6b8b")


def configure_theme_tokens(tokens: Any) -> None:
    """Inject host theme tokens without importing the host style module."""
    global COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY, COLOR_TEXT_MUTED
    global COLOR_BG_SURFACE, COLOR_BG_INSET, COLOR_BG_ELEVATED
    global COLOR_BORDER, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, COLOR_ACCENT

    COLOR_TEXT_PRIMARY = tokens.color_text_primary
    COLOR_TEXT_SECONDARY = tokens.color_text_secondary
    COLOR_TEXT_MUTED = tokens.color_text_muted
    COLOR_BG_SURFACE = tokens.color_bg_surface
    COLOR_BG_INSET = tokens.color_bg_inset
    COLOR_BG_ELEVATED = tokens.color_bg_elevated
    COLOR_BORDER = tokens.color_border
    COLOR_SUCCESS = tokens.color_success
    COLOR_ERROR = tokens.color_error
    COLOR_WARNING = tokens.color_warning
    COLOR_ACCENT = tokens.color_accent


def pick(color_pair: tuple[str, str]) -> str:
    """Return light or dark value for the active qfluentwidgets theme."""
    return color_pair[1] if isDarkTheme() else color_pair[0]


def apply_themed_style(widget, light_qss: str, dark_qss: str) -> None:
    """Apply qfluentwidgets-aware themed QSS."""
    setCustomStyleSheet(widget, light_qss, dark_qss)

# ── 素材卡片尺寸 ────────────────────────────────────────────
# 与 _mext/core/constants.py 中的 MATERIAL_CARD_WIDTH/HEIGHT 保持一致

CARD_WIDTH: int = 220
CARD_HEIGHT: int = 280
CARD_IMAGE_HEIGHT: int = 140   # 卡片顶部预览图高度（≈ 63% 卡片高）
CARD_BORDER_RADIUS: int = 8

# ── 通用间距 ────────────────────────────────────────────────

SPACING_XS: int = 4
SPACING_SM: int = 8
SPACING_MD: int = 12
SPACING_LG: int = 16
SPACING_XL: int = 24

# ── FlowLayout 卡片网格间距 ──────────────────────────────────

GRID_H_SPACING: int = 12
GRID_V_SPACING: int = 12

# ── 侧边过滤面板宽度 ─────────────────────────────────────────

FILTER_PANEL_WIDTH: int = 200

# ── 排序/通用 ComboBox 宽度 ──────────────────────────────────

COMBO_WIDTH_SM: int = 120      # 小型（如并发数，只有几个数字选项）
COMBO_WIDTH_MD: int = 150      # 中型（如排序方式）
COMBO_WIDTH_LG: int = 200      # 标准（与主应用统一）

# ── 卡片选中态颜色（用于 USB 设备卡片） ─────────────────────
# themeColor() 动态获取，这里只作 fallback

COLOR_SELECTION_BORDER = ("#ff6b8b", "#ff8fa3")   # (light, dark)

# ── 占位图颜色（替代硬编码 lightGray） ──────────────────────

COLOR_PLACEHOLDER_BG = ("#e0e0e0", "#3a3a3a")    # (light, dark)
COLOR_PLACEHOLDER_FG = ("#aaaaaa", "#666666")    # (light, dark)

# ── 画廊卡片 (Gallery / Waterfall) ─────────────────────────

GALLERY_CARD_MIN_WIDTH: int = 240
GALLERY_CARD_MAX_WIDTH: int = 380
GALLERY_GRID_SPACING: int = 16
GALLERY_CARD_BORDER_RADIUS: int = 12

# ── 头像尺寸 ──────────────────────────────────────────────

AVATAR_SM: int = 24     # 卡片内小头像
AVATAR_MD: int = 36     # 评论区头像
AVATAR_LG: int = 48     # 详情页创作者头像

# ── 详情页 ────────────────────────────────────────────────

DETAIL_MAX_WIDTH: int = 1200
DETAIL_IMAGE_MAX_HEIGHT: int = 600
DETAIL_SIDEBAR_WIDTH: int = 320

# ── Hover 遮罩颜色 ────────────────────────────────────────

COLOR_HOVER_OVERLAY = ("rgba(0,0,0,0.4)", "rgba(0,0,0,0.5)")

# ── 评论区 ──────────────────────────────────────────────

COMMENT_INPUT_MIN_HEIGHT: int = 60
COMMENT_INPUT_MAX_HEIGHT: int = 120
COMMENT_BUBBLE_PADDING: int = 12

# ── 创作者页 ────────────────────────────────────────────

CREATOR_AVATAR_XL: int = 80
CREATOR_HEADER_HEIGHT: int = 200

# ── 精选区 ──────────────────────────────────────────────

FEATURED_BANNER_HEIGHT: int = 200
FEATURED_CARD_WIDTH: int = 280

# ── 相关素材 ────────────────────────────────────────────

RELATED_SECTION_HEIGHT: int = 280
RELATED_CARD_WIDTH: int = 200
