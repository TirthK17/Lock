"""Tests for U2 reusable UI components (buttons, Toggle, SegmentedControl, Spinner, DialogShell, States)."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mylocker.app.ui.components import (
    Badge,
    Card,
    DangerButton,
    DialogShell,
    EmptyState,
    ErrorState,
    IconButton,
    ListRow,
    PrimaryButton,
    SearchBar,
    SecondaryButton,
    SectionHeader,
    SegmentedControl,
    SkeletonRow,
    Spinner,
    Toast,
    Toggle,
)


def test_buttons_have_variants_and_no_glow_effects(qtbot):
    p = PrimaryButton("Save")
    s = SecondaryButton("Cancel")
    d = DangerButton("Delete")
    i = IconButton("settings", tooltip="Settings")
    for w in (p, s, d, i):
        qtbot.addWidget(w)
        assert w.graphicsEffect() is None
    assert p.property("variant") == "primary"
    assert s.property("variant") == "secondary"
    assert d.property("variant") == "danger"
    assert i.property("variant") == "icon"
    assert i.accessibleName() == "Settings"


def test_toggle_animated_and_reduced_motion(qtbot, monkeypatch):
    monkeypatch.setattr("mylocker.app.ui.components._reduced_motion", lambda: True)
    t = Toggle("Enable feature")
    qtbot.addWidget(t)
    t.show()
    assert t.isChecked() is False

    seen: list[bool] = []
    t.toggled.connect(seen.append)
    qtbot.mouseClick(t, Qt.MouseButton.LeftButton)
    assert t.isChecked() is True
    assert seen == [True]
    assert t.knobPos == 1.0


def test_segmented_control_switches_and_emits(qtbot):
    seg = SegmentedControl(
        [("grid", "Grid", "grid"), ("list", "List", "list")],
        current="grid",
    )
    qtbot.addWidget(seg)
    seen: list[str] = []
    seg.selection_changed.connect(seen.append)

    qtbot.mouseClick(seg._buttons["list"], Qt.MouseButton.LeftButton)
    assert seg.current() == "list"
    assert seen == ["list"]


def test_search_bar_empty_error_spinner_skeleton_toast(qtbot):
    sb = SearchBar("Find files…")
    qtbot.addWidget(sb)
    sb.show()
    assert not sb._clear_btn.isVisible()
    sb.setText("report")
    assert sb._clear_btn.isVisible()
    sb._clear_btn.click()
    assert sb.text() == ""

    empty = EmptyState("folder", "No files", "Drop files here", "Add Files")
    err = ErrorState("Failed", "Disk error", "Retry")
    spin = Spinner()
    skel = SkeletonRow()
    toast = Toast("Saved successfully", variant="success", duration_ms=1500)
    card = Card(interactive=True)
    row = ListRow("Auto-lock", "Locks vault automatically", Toggle(), icon_name="lock")
    badge = Badge("Active", variant="success")
    hdr = SectionHeader("Security")
    for w in (empty, err, spin, skel, toast, card, row, badge, hdr):
        qtbot.addWidget(w)

    spin.start()
    assert spin.isVisible()
    spin.hide()
    assert not spin._timer.isActive()

    toast.show_toast()
    assert toast.isVisible()
    toast.dismiss()
    assert not toast.isVisible()


def test_dialog_shell_respects_mocked_qmessagebox_confirm():
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as mock_q:
        ok = DialogShell.confirm(None, "Delete", "Remove selected?")
        assert ok is True
        mock_q.assert_called_once()
