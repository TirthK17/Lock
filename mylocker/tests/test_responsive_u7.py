"""U7 Responsive 900x600, 100%-175% scaling, filename elision, and high-DPI icon tests."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QScrollArea

from mylocker.app.core.index import FileEntry
from mylocker.app.ui import icons
from mylocker.app.ui.applock_ui import AppLockUI
from mylocker.app.ui.calculator_disguise import CalculatorDisguise
from mylocker.app.ui.dashboard import Dashboard
from mylocker.app.ui.dashboard_theme import DocBadgeGenerator, MediaBadgeGenerator
from mylocker.app.ui.design_system import Tokens, apply_theme, build_global_qss
from mylocker.app.ui.file_grid import FileCard, FileListModel
from mylocker.app.ui.gallery import GalleryViewer
from mylocker.app.ui.intruder_log import IntruderLogUI
from mylocker.app.ui.login import LoginScreen
from mylocker.app.ui.settings import SettingsUI
from mylocker.app.ui.setup_wizard import SetupWizard


@pytest.fixture(autouse=True)
def _u7_env(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    orig_font = app.font() if app is not None else None
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)
    if app is not None:
        apply_theme(app, "dark")
        if orig_font is not None:
            app.setFont(orig_font)


def _assert_no_horizontal_overflow_or_clip(root, label: str, scale: float) -> None:
    for sa in root.findChildren(QScrollArea):
        if sa.isVisible():
            hsb = sa.horizontalScrollBar()
            if hsb is not None:
                assert hsb.maximum() == 0, (
                    f"{label} @{scale:.2f}x has horizontal overflow: max={hsb.maximum()} width={sa.width()}"
                )
    assert root.minimumSizeHint().width() <= root.width() + 2, (
        f"{label} @{scale:.2f}x minimumSizeHint.width={root.minimumSizeHint().width()} > {root.width()}"
    )
    for w in root.findChildren(QAbstractButton) + root.findChildren(QLabel):
        if not w.isVisible() or not w.text():
            continue
        fm = w.fontMetrics()
        assert w.height() + 1 >= fm.height(), (
            f"{label} @{scale:.2f}x clipped vertically on {type(w).__name__}({w.objectName()}): "
            f"h={w.height()} < fm={fm.height()} text={w.text()[:24]!r}"
        )


def _make_mocks(tmp_path: Path):
    ks = MagicMock()
    ks.vault_exists.return_value = True
    ks.has_keyfile.return_value = False

    lm = MagicMock()
    lm.is_locked_out.return_value = False
    lm.remaining_seconds.return_value = 0
    lm.get_lockout_seconds.return_value = 0
    lm.get_intruder_log.return_value = []
    lm.get_intruder_logs.return_value = []

    ss = MagicMock()
    ss.get.side_effect = lambda _k, default=None: default

    vault = MagicMock()
    vault.is_unlocked = True
    vault.vault_dir = tmp_path
    vault._index.index.all_files.return_value = []
    vault._index.index.get.return_value = None
    vault.list_entries.return_value = []
    vault.list_trash.return_value = []
    vault.get_total_size.return_value = 0

    session = MagicMock()
    session.is_unlocked = True
    session.key = b"\x00" * 32
    return ks, lm, ss, vault, session


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75])
def test_screens_responsive_900x600_across_scales(qtbot, tmp_path, scale):
    app = QApplication.instance()
    assert app is not None

    scaled_css = f"* {{ font-size: {round(Tokens.type.BODY * scale)}px; }}" if scale != 1.0 else ""
    app.setStyleSheet(build_global_qss() + scaled_css)
    f = Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY)
    f.setPixelSize(round(Tokens.type.BODY * scale))
    app.setFont(f)

    ks, lm, ss, vault, session = _make_mocks(tmp_path)

    login = LoginScreen(keystore=ks, lockout_manager=lm, settings_store=ss)
    qtbot.addWidget(login)
    login.resize(900, 600)
    login.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(login, "LoginScreen", scale)
    login.close()

    wiz = SetupWizard(ks, str(tmp_path))
    qtbot.addWidget(wiz)
    wiz.resize(900, 600)
    wiz.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(wiz, "SetupWizard", scale)
    wiz.close()

    dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
    qtbot.addWidget(dash)
    assert dash.minimumWidth() <= 900
    assert dash.minimumHeight() <= 600
    dash.resize(900, 600)
    dash.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(dash, "Dashboard_expanded", scale)
    dash._sidebar.set_collapsed(True)
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(dash, "Dashboard_collapsed", scale)
    dash.close()

    settings = SettingsUI(vault, ks, settings_store=ss)
    qtbot.addWidget(settings)
    settings.resize(620, 520)
    settings.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(settings, "SettingsUI", scale)
    settings.close()

    intr = IntruderLogUI(lm)
    qtbot.addWidget(intr)
    intr.resize(620, 520)
    intr.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(intr, "IntruderLogUI", scale)
    intr.close()

    applock = AppLockUI([])
    qtbot.addWidget(applock)
    applock.resize(620, 520)
    applock.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(applock, "AppLockUI", scale)
    applock.close()

    gal = GalleryViewer("img1", bytearray(), vault)
    qtbot.addWidget(gal)
    gal.resize(900, 600)
    gal.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(gal, "GalleryViewer", scale)
    gal.close()

    calc = CalculatorDisguise()
    qtbot.addWidget(calc)
    calc.resize(900, 600)
    calc.show()
    app.processEvents()
    _assert_no_horizontal_overflow_or_clip(calc, "CalculatorDisguise", scale)
    calc.close()


def test_long_filename_elides_with_full_tooltip(qtbot):
    long_name = "quarterly_confidential_audit_report_fiscal_year_2026_final_signed_approved_v12.pdf"
    entry = FileEntry(
        file_id="f_long",
        original_name=long_name,
        original_path=f"C:/tmp/{long_name}",
        vault_path="",
        size_bytes=2048,
        modified_time=1700000000.0,
        mime_type="application/pdf",
    )
    cache: OrderedDict = OrderedDict()
    card = FileCard(entry, thumb_cache=cache)
    qtbot.addWidget(card)

    rendered = card._name_lbl.text()
    assert rendered != long_name
    assert "…" in rendered
    assert long_name in card.toolTip()
    assert card._name_lbl.toolTip() == long_name

    model = FileListModel()
    model.set_entries([entry])
    idx = model.index(0, 0)
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == long_name
    assert long_name in model.data(idx, Qt.ItemDataRole.ToolTipRole)


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 1.75])
def test_icons_and_badges_crisp_at_high_dpi(qtbot, dpr):
    app = QApplication.instance()
    assert app is not None

    px = icons.get_pixmap("shield_check", size=20, dpr=dpr)
    assert px.width() == round(20 * dpr)
    assert px.height() == round(20 * dpr)
    assert pytest.approx(px.devicePixelRatio(), rel=1e-2) == dpr

    # Simulate application DPR so DocBadgeGenerator & MediaBadgeGenerator match physical resolution
    DocBadgeGenerator._CACHE.clear()
    MediaBadgeGenerator._CACHE.clear()
    orig_dpr = app.devicePixelRatio
    app.devicePixelRatio = lambda: dpr  # type: ignore[method-assign]
    try:
        badge = DocBadgeGenerator.get_badge("pdf", 180, 100)
        img_ph = MediaBadgeGenerator.get_image_placeholder(164, 96)
        vid_ph = MediaBadgeGenerator.get_video_placeholder(164, 96)
        assert badge.width() == round(180 * dpr)
        assert img_ph.width() == round(164 * dpr)
        assert vid_ph.width() == round(164 * dpr)
        assert pytest.approx(badge.devicePixelRatio(), rel=1e-2) == dpr
        assert pytest.approx(img_ph.devicePixelRatio(), rel=1e-2) == dpr
        assert pytest.approx(vid_ph.devicePixelRatio(), rel=1e-2) == dpr
    finally:
        app.devicePixelRatio = orig_dpr  # type: ignore[method-assign]
        DocBadgeGenerator._CACHE.clear()
        MediaBadgeGenerator._CACHE.clear()
