"""Host-side helpers for loading optional embedded plugins."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from PyQt6.QtWidgets import QWidget

from gui import styles as host_styles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThemeTokens:
    color_text_primary: tuple[str, str]
    color_text_secondary: tuple[str, str]
    color_text_muted: tuple[str, str]
    color_bg_surface: tuple[str, str]
    color_bg_inset: tuple[str, str]
    color_bg_elevated: tuple[str, str]
    color_border: tuple[str, str]
    color_success: tuple[str, str]
    color_error: tuple[str, str]
    color_warning: tuple[str, str]
    color_accent: tuple[str, str]


@dataclass(frozen=True)
class PluginContext:
    theme_tokens: ThemeTokens
    settings: dict[str, Any]
    locale: str = "zh_CN"
    tr: Callable[[str], str] = lambda value: value


@dataclass
class PluginHandle:
    """Host-owned lifecycle boundary for an embedded plugin widget."""

    widget: QWidget
    context: PluginContext
    _is_shutdown: bool = False

    def shutdown(self, timeout_ms: int = 2000) -> bool:
        """Best-effort plugin shutdown.

        Plugin failures are logged and swallowed so application exit is not
        blocked by extension cleanup issues.
        """
        if self._is_shutdown:
            return True

        self._is_shutdown = True
        shutdown = getattr(self.widget, "shutdown", None)
        if not callable(shutdown):
            return True

        try:
            try:
                shutdown(timeout_ms=timeout_ms)
            except TypeError:
                shutdown()
            return True
        except Exception:
            logger.exception("Plugin shutdown failed")
            return False

    def apply_theme(self) -> bool:
        """Re-apply host theme tokens to the plugin."""
        try:
            _configure_material_forum_theme(self.context)
            apply_theme = getattr(self.widget, "apply_theme", None)
            if callable(apply_theme):
                apply_theme()
            elif hasattr(self.widget, "update"):
                self.widget.update()
            return True
        except Exception:
            logger.exception("Plugin theme application failed")
            return False

    def health(self) -> dict[str, Any]:
        """Return a small diagnostic snapshot for host-side checks."""
        service_manager = getattr(self.widget, "service_manager", None)
        service_shutdown = None
        if service_manager is not None:
            service_shutdown = getattr(service_manager, "is_shutdown", None)

        return {
            "widget_class": type(self.widget).__name__,
            "shutdown": self._is_shutdown,
            "service_shutdown": service_shutdown,
        }


def default_plugin_context(
    settings: dict[str, Any] | None = None,
    *,
    locale: str | None = None,
    tr: Callable[[str], str] | None = None,
) -> PluginContext:
    resolved_settings = settings or {}
    return PluginContext(
        theme_tokens=_host_theme_tokens(),
        settings=resolved_settings,
        locale=locale or _resolve_locale(resolved_settings),
        tr=tr or (lambda value: value),
    )


def create_material_forum_plugin(
    *,
    parent: QWidget,
    context: PluginContext | None = None,
) -> PluginHandle:
    """Create the material forum plugin through a host-controlled boundary."""
    context = context or default_plugin_context()
    _configure_material_forum_theme(context)

    from _mext.ui.widget import MaterialForumWidget

    widget = MaterialForumWidget(parent=parent)
    return PluginHandle(widget=widget, context=context)


def create_material_forum_widget(
    *,
    parent: QWidget,
    context: PluginContext | None = None,
) -> QWidget:
    """Compatibility wrapper returning only the plugin widget."""
    return create_material_forum_plugin(parent=parent, context=context).widget


def _configure_material_forum_theme(context: PluginContext) -> None:
    from _mext.ui import styles as mext_styles

    mext_styles.configure_theme_tokens(context.theme_tokens)


def _resolve_locale(settings: dict[str, Any]) -> str:
    language = settings.get("language") or settings.get("locale")
    if language in {"zh_CN", "en_US"}:
        return language
    if language in {"中文", "简体中文", "Chinese"}:
        return "zh_CN"
    if language in {"English", "英文"}:
        return "en_US"
    return "zh_CN"


def _host_theme_tokens() -> ThemeTokens:
    return ThemeTokens(
        color_text_primary=host_styles.COLOR_TEXT_PRIMARY,
        color_text_secondary=host_styles.COLOR_TEXT_SECONDARY,
        color_text_muted=host_styles.COLOR_TEXT_MUTED,
        color_bg_surface=host_styles.COLOR_BG_SURFACE,
        color_bg_inset=host_styles.COLOR_BG_INSET,
        color_bg_elevated=host_styles.COLOR_BG_ELEVATED,
        color_border=host_styles.COLOR_BORDER,
        color_success=host_styles.COLOR_SUCCESS,
        color_error=host_styles.COLOR_ERROR,
        color_warning=host_styles.COLOR_WARNING,
        color_accent=host_styles.COLOR_ACCENT,
    )
