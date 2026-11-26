"""
motion.py — Centralized, restrained Qt animation helpers (120–220 ms, OutCubic).

All helpers:
  - Honor Windows reduced motion (`SPI_GETCLIENTAREAANIMATION`) and `reduced_motion` setting.
  - Parent animations to target QWidgets using KeepWhenStopped + `shiboken6.isValid`
    (never DeleteWhenStopped with dynamic properties) so zero dangling C++ pointers occur.
  - Guard against offscreen/worker threads (no animations created on worker threads).
  - Avoid blur/shadow effects on large areas; detach transient opacity effects cleanly.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

import shiboken6
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QThread,
    QVariantAnimation,
)
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QStackedWidget, QWidget

from .design_system import Tokens

_SPI_GETCLIENTAREAANIMATION = 0x1042


def _on_gui_thread() -> bool:
    app = QApplication.instance()
    if app is None:
        return True
    return QThread.currentThread() == app.thread()


def is_reduced_motion(settings_store=None) -> bool:
    """Respect explicit reduced-motion env/settings flags and Windows animation setting."""
    if os.environ.get("MYLOCKER_FORCE_REDUCED_MOTION") == "1":
        return True
    if settings_store is not None:
        try:
            if bool(settings_store.get("reduced_motion", False)):
                return True
        except Exception:
            pass
    try:
        import ctypes
        from ctypes import wintypes

        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            user32 = ctypes.windll.user32
            user32.SystemParametersInfoW.argtypes = [
                wintypes.UINT,
                wintypes.UINT,
                ctypes.c_void_p,
                wintypes.UINT,
            ]
            user32.SystemParametersInfoW.restype = wintypes.BOOL
            val = wintypes.BOOL()
            if user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(val), 0):
                if not val.value:
                    return True
    except Exception:
        pass
    return False


def _effective_reduced_motion() -> bool:
    """Check both motion.is_reduced_motion and any test-monkeypatched login_theme override."""
    if is_reduced_motion():
        return True
    lt = sys.modules.get("mylocker.app.ui.login_theme")
    if lt is not None:
        fn = getattr(lt, "is_reduced_motion", None)
        if fn is not None and fn is not is_reduced_motion:
            try:
                if fn():
                    return True
            except Exception:
                pass
    return False


def _stop_slot(widget: QWidget, slot: str) -> None:
    if not shiboken6.isValid(widget):
        return
    existing = getattr(widget, slot, None)
    if existing is not None and shiboken6.isValid(existing) and isinstance(existing, QAbstractAnimation):
        try:
            existing.stop()
        except RuntimeError:
            pass
    setattr(widget, slot, None)


def _clamp_duration(duration_ms: int) -> int:
    return max(Tokens.motion.FAST, min(Tokens.motion.SLOW, int(duration_ms)))


def _attach_transient_opacity(widget: QWidget, start_opacity: float = 0.0) -> Optional[QGraphicsOpacityEffect]:
    # Never replace an existing drop-shadow effect (e.g. DialogShell, Toast, QMenu).
    cur = widget.graphicsEffect()
    if cur is not None:
        if isinstance(cur, QGraphicsOpacityEffect):
            cur.setOpacity(start_opacity)
            return cur
        return None
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(start_opacity)
    widget.setGraphicsEffect(eff)
    return eff


def fade_in(
    widget: QWidget,
    duration_ms: int = Tokens.motion.NORMAL,
    *,
    remove_effect_on_finish: bool = True,
) -> Optional[QPropertyAnimation]:
    """Fade a widget from 0 -> 1 cleanly using QPropertyAnimation (OutCubic)."""
    if not shiboken6.isValid(widget):
        return None
    _stop_slot(widget, "_motion_fade_anim")
    if not _on_gui_thread() or _effective_reduced_motion():
        if remove_effect_on_finish and isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)
        return None

    if widget.isWindow() and widget.graphicsEffect() is not None and not widget.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground
    ):
        widget.setWindowOpacity(0.0)
        anim = QPropertyAnimation(widget, b"windowOpacity", widget)
        anim.setDuration(_clamp_duration(duration_ms))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        widget._motion_fade_anim = anim  # type: ignore[attr-defined]
        anim.start()
        return anim

    eff = _attach_transient_opacity(widget, 0.0)
    if eff is None:
        return None

    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    if remove_effect_on_finish:
        def _clear() -> None:
            if shiboken6.isValid(widget) and shiboken6.isValid(eff):
                try:
                    if widget.graphicsEffect() is eff:
                        widget.setGraphicsEffect(None)
                except RuntimeError:
                    pass
        anim.finished.connect(_clear)
    widget._motion_fade_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def slide_fade(
    widget: QWidget,
    offset_y: int = 8,
    duration_ms: int = Tokens.motion.NORMAL,
) -> Optional[QParallelAnimationGroup]:
    """Subtle vertical slide + opacity fade-in using QParallelAnimationGroup."""
    if not shiboken6.isValid(widget):
        return None
    _stop_slot(widget, "_motion_slide_anim")
    if not _on_gui_thread() or _effective_reduced_motion():
        return None

    group = QParallelAnimationGroup(widget)
    dur = _clamp_duration(duration_ms)

    target_pos = widget.pos()
    widget.move(QPoint(target_pos.x(), target_pos.y() + offset_y))
    pos_anim = QPropertyAnimation(widget, b"pos", group)
    pos_anim.setDuration(dur)
    pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    pos_anim.setStartValue(QPoint(target_pos.x(), target_pos.y() + offset_y))
    pos_anim.setEndValue(target_pos)
    group.addAnimation(pos_anim)

    eff = _attach_transient_opacity(widget, 0.0)
    if eff is not None:
        op_anim = QPropertyAnimation(eff, b"opacity", group)
        op_anim.setDuration(dur)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)
        group.addAnimation(op_anim)

        def _cleanup_eff() -> None:
            if shiboken6.isValid(widget) and shiboken6.isValid(eff):
                try:
                    if widget.graphicsEffect() is eff:
                        widget.setGraphicsEffect(None)
                except RuntimeError:
                    pass
        group.finished.connect(_cleanup_eff)

    widget._motion_slide_anim = group  # type: ignore[attr-defined]
    group.start()
    return group


def animate_indicator_slide(
    indicator: QWidget,
    target: QWidget,
    duration_ms: int = Tokens.motion.FAST,
) -> Optional[QPropertyAnimation]:
    """Slide sidebar active pill cleanly to the target item's geometry."""
    if not (shiboken6.isValid(indicator) and shiboken6.isValid(target)):
        return None
    geo = target.geometry()
    if geo.width() <= 0 or geo.height() <= 0:
        return None
    indicator.show()
    indicator.lower()
    _stop_slot(indicator, "_motion_indicator_anim")

    if (
        not _on_gui_thread()
        or _effective_reduced_motion()
        or not indicator.isVisible()
        or indicator.width() <= 0
    ):
        indicator.setGeometry(geo)
        return None

    anim = QPropertyAnimation(indicator, b"geometry", indicator)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(indicator.geometry())
    anim.setEndValue(geo)
    indicator._motion_indicator_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def press_feedback(button: QWidget, pressed: bool, duration_ms: int = Tokens.motion.FAST) -> None:
    """Restrained opacity pulse on press without geometry scaling blur."""
    if not shiboken6.isValid(button) or not _on_gui_thread() or _effective_reduced_motion():
        return
    eff = button.graphicsEffect()
    if eff is None or not isinstance(eff, QGraphicsOpacityEffect):
        return
    _stop_slot(button, "_motion_press_anim")
    anim = QPropertyAnimation(eff, b"opacity", button)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(eff.opacity())
    anim.setEndValue(0.88 if pressed else 1.0)
    button._motion_press_anim = anim  # type: ignore[attr-defined]
    anim.start()


def _window_opacity_safe(win: QWidget) -> bool:
    if not shiboken6.isValid(win):
        return False
    if win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
        return False
    return True


def toast_in(toast: QWidget, duration_ms: int = Tokens.motion.NORMAL) -> Optional[QPropertyAnimation]:
    """Present toast smoothly while respecting reduced motion."""
    if not shiboken6.isValid(toast):
        return None
    _stop_slot(toast, "_motion_toast_anim")
    toast.show()
    toast.raise_()
    if not _on_gui_thread() or _effective_reduced_motion() or not _window_opacity_safe(toast):
        return None
    toast.setWindowOpacity(0.0)
    anim = QPropertyAnimation(toast, b"windowOpacity", toast)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    toast._motion_toast_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def toast_out(
    toast: QWidget,
    on_finished: Optional[Callable[[], None]] = None,
    duration_ms: int = Tokens.motion.FAST,
) -> Optional[QPropertyAnimation]:
    """Fade out toast cleanly, invoking `on_finished` when hidden."""
    if not shiboken6.isValid(toast):
        return None
    _stop_slot(toast, "_motion_toast_anim")
    if (
        not _on_gui_thread()
        or _effective_reduced_motion()
        or not toast.isVisible()
        or not _window_opacity_safe(toast)
    ):
        toast.hide()
        if on_finished is not None:
            on_finished()
        return None
    anim = QPropertyAnimation(toast, b"windowOpacity", toast)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(toast.windowOpacity())
    anim.setEndValue(0.0)

    def _done() -> None:
        if shiboken6.isValid(toast):
            try:
                toast.hide()
                toast.setWindowOpacity(1.0)
            except RuntimeError:
                pass
        if on_finished is not None:
            on_finished()

    anim.finished.connect(_done)
    toast._motion_toast_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def modal_rise(dialog: QWidget, duration_ms: int = Tokens.motion.NORMAL) -> Optional[QPropertyAnimation]:
    """Gentle fade-in for modal dialogs; avoids windowOpacity on translucent frameless shells."""
    if not shiboken6.isValid(dialog):
        return None
    _stop_slot(dialog, "_motion_modal_anim")
    if not _on_gui_thread() or _effective_reduced_motion():
        return None
    body = getattr(dialog, "body_widget", None)
    if dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
        if isinstance(body, QWidget):
            return fade_in(body, duration_ms=duration_ms, remove_effect_on_finish=True)
        return None
    dialog.setWindowOpacity(0.0)
    anim = QPropertyAnimation(dialog, b"windowOpacity", dialog)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    dialog._motion_modal_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def dropdown_open(menu: QWidget, duration_ms: int = Tokens.motion.FAST) -> Optional[QPropertyAnimation]:
    """Subtle menu fade-in respecting reduced motion and translucent attributes."""
    if not shiboken6.isValid(menu):
        return None
    _stop_slot(menu, "_motion_menu_anim")
    if not _on_gui_thread() or _effective_reduced_motion() or not _window_opacity_safe(menu):
        return None
    menu.setWindowOpacity(0.0)
    anim = QPropertyAnimation(menu, b"windowOpacity", menu)
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    menu._motion_menu_anim = anim  # type: ignore[attr-defined]
    anim.start()
    return anim


def animate_toggle(
    anim: QVariantAnimation,
    start: float,
    end: float,
    on_immediate: Callable[[float], None],
    duration_ms: int = Tokens.motion.FAST,
) -> None:
    """Animate sliding knob value cleanly with reduced-motion fallback."""
    if not shiboken6.isValid(anim):
        return
    anim.stop()
    if not _on_gui_thread() or _effective_reduced_motion():
        on_immediate(end)
        return
    anim.setDuration(_clamp_duration(duration_ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.start()


def crossfade_stack_page(
    stack: QStackedWidget,
    index: int,
    duration_ms: int = Tokens.motion.NORMAL,
) -> None:
    """Switch QStackedWidget page cleanly with fast OutCubic transition."""
    if not shiboken6.isValid(stack) or not (0 <= index < stack.count()):
        return
    if stack.currentIndex() == index:
        return
    stack.setCurrentIndex(index)
    target = stack.widget(index)
    if target is not None and not _effective_reduced_motion():
        fade_in(target, duration_ms=duration_ms, remove_effect_on_finish=True)


def stop_all_animations(root: QWidget) -> None:
    """Stop all QAbstractAnimation children parented under `root`."""
    if not shiboken6.isValid(root):
        return
    for anim in root.findChildren(QAbstractAnimation):
        if shiboken6.isValid(anim):
            try:
                anim.stop()
            except RuntimeError:
                pass


__all__ = [
    "is_reduced_motion",
    "fade_in",
    "slide_fade",
    "animate_indicator_slide",
    "press_feedback",
    "toast_in",
    "toast_out",
    "modal_rise",
    "dropdown_open",
    "animate_toggle",
    "crossfade_stack_page",
    "stop_all_animations",
]
