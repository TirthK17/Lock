"""
test_video_player_ui.py — Comprehensive offscreen UI, playback, layout, and security
regression tests for the redesigned VideoPlayer.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtMultimedia import QMediaPlayer

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault
from mylocker.app.ui.video_player import (
    StreamingVaultIODevice,
    VideoPlayer,
    _format_duration_ms,
    _format_size,
    _quality_tier_from_size,
)


def _make_sample_mp4_bytes() -> bytes:
    """Create minimal MP4-like bytes (safe dummy payload to store in encrypted vault)."""
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + os.urandom(4096)


@pytest.fixture(autouse=True)
def mock_qmediaplayer_backend():
    with patch.object(QMediaPlayer, "play"), patch.object(QMediaPlayer, "pause"), patch.object(QMediaPlayer, "setSourceDevice"):
        yield


@pytest.fixture
def vault_with_video(tmp_path):
    vault_dir = tmp_path / "VideoVault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    keystore = Keystore(vault_dir)
    with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
        mock_bench.return_value = KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)
        mk, _ = keystore.setup("VideoPlayerPass123#", benchmark=False)

    session = get_session()
    session.unlock(mk, is_decoy=False)

    vault = Vault(vault_dir)
    vault.initialise()

    src = tmp_path / "Project_Showcase_Final_Render_2026_Ultra_Cinema.mp4"
    src.write_bytes(_make_sample_mp4_bytes())
    entry = vault.add_file(src, vault_folder="/", generate_thumbnail=False)

    yield vault_dir, vault, entry, session

    session.lock()


class TestVideoHelpers:
    def test_duration_and_size_formatting(self):
        assert _format_duration_ms(0) == "00:00"
        assert _format_duration_ms(267_000) == "04:27"
        assert _format_duration_ms(3_723_000) == "1:02:03"
        assert _format_size(512) == "512 B"
        assert _format_size(1_536_000) == "1.5 MB"
        assert _format_size(1_288_490_188) == "1.2 GB"

    def test_quality_tiers(self):
        assert _quality_tier_from_size(3840, 2160) == "4K"
        assert _quality_tier_from_size(2560, 1440) == "1440p"
        assert _quality_tier_from_size(1920, 1080) == "1080p"
        assert _quality_tier_from_size(1280, 720) == "720p"
        assert _quality_tier_from_size(854, 480) == "480p"
        assert _quality_tier_from_size(320, 240) == "SD"


class TestVideoPlayerUI:
    def test_metadata_top_bar_bookmark_info_and_cleanup(self, qtbot, vault_with_video):
        _dir, vault, entry, _session = vault_with_video

        with patch.object(QMediaPlayer, "play"):
            player = VideoPlayer(entry.file_id, vault)
            try:
                player.show()
                qtbot.wait(30)

                # Filename elides gracefully & quality/format subtitle renders
                assert "Project_Showcase" in player.windowTitle()
                assert "Project_Showcase" in player._title_lbl.text() or "…" in player._title_lbl.text()
                assert "MP4" in player._subtitle_lbl.text()
                assert player._quality_pill.text() == "HD"

                # Simulated 4K video resolution updates header + badge + info panel
                player._video_size = QSize(3840, 2160)
                player._on_duration_changed(736_000)  # 12:16
                assert "4K" in player._subtitle_lbl.text()
                assert "3840 × 2160" in player._subtitle_lbl.text()
                assert player._quality_pill.text() == "4K"
                assert player._time_lbl.text() == "00:00 / 12:16"

                # Bookmark / Favorite toggles & persists into encrypted index
                emitted = []
                player.favorite_changed.connect(lambda fid, st: emitted.append((fid, st)))
                player._bookmark_btn.click()
                assert emitted == [(entry.file_id, True)]
                assert vault._index.index.get(entry.file_id).is_favorite is True

                # Toggle Info panel
                assert player._info_panel.isHidden()
                player._info_btn.click()
                assert not player._info_panel.isHidden()
                assert player._info_panel._rows["Resolution"].text() == "3840 × 2160"
                assert player._info_panel._rows["Duration"].text() == "12:16"

                # Keep reference to IO device to assert zeroization on close
                io_dev = player._io_device
                assert isinstance(io_dev, StreamingVaultIODevice)
                assert len(io_dev._file_key) == 32

                player.close()
                qtbot.wait(30)

                assert player._cleaned_up is True
                assert len(io_dev._file_key) == 0
                assert not io_dev.isOpen()
                assert len(player._thumb_cache) == 0
            finally:
                if not player._cleaned_up:
                    player.close()

    def test_controls_shortcuts_speed_volume_scrubber_and_error_recovery(self, qtbot, vault_with_video):
        _dir, vault, entry, _session = vault_with_video

        with patch.object(QMediaPlayer, "play"), patch.object(QMediaPlayer, "pause"):
            player = VideoPlayer(entry.file_id, vault)
            try:
                player.show()
                qtbot.wait(20)

                player._on_duration_changed(120_000)

                # Settings popover & speed change
                player._settings_btn.click()
                assert not player._settings_popover.isHidden()
                player._settings_popover._speed_buttons[1.5].click()
                assert player._playback_rate == pytest.approx(1.5)

                # Volume expand/collapse & mute shortcut 'M'
                player._vol_cluster.expand()
                qtbot.wait(210)
                assert player._vol_cluster._slider.width() == 90

                qtbot.keyClick(player, Qt.Key.Key_M)
                assert player._is_muted is True
                assert player._audio_output.isMuted() is True
                qtbot.keyClick(player, Qt.Key.Key_M)
                assert player._is_muted is False

                # Arrow Up / Down adjusts volume
                before_vol = player._last_volume
                qtbot.keyClick(player, Qt.Key.Key_Down)
                assert player._last_volume == before_vol - 5
                qtbot.keyClick(player, Qt.Key.Key_Up)
                assert player._last_volume == before_vol

                # Seek relative via keyboard Right / Left
                with patch.object(player._player, "setPosition") as mock_set_pos, patch.object(
                    player._player, "position", return_value=30_000
                ):
                    qtbot.keyClick(player, Qt.Key.Key_Right)
                    mock_set_pos.assert_called_with(40_000)
                    qtbot.keyClick(player, Qt.Key.Key_Left)
                    mock_set_pos.assert_called_with(20_000)

                # Scrubber hover tooltip appears cleanly
                player._scrubber.set_duration(120_000)
                player._on_scrubber_hover(60_000, player._scrubber.width() // 2)
                assert not player._scrub_preview.isHidden()
                assert player._scrub_preview._time_text == "01:00"
                player._on_scrubber_leave()
                assert player._scrub_preview.isHidden()

                # Buffering keeps player responsive & shows minimal loader without wiping frame
                player._on_media_status_changed(QMediaPlayer.MediaStatus.BufferingMedia)
                assert not player._loading_overlay.isHidden()
                player._on_media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
                assert player._loading_overlay.isHidden()

                # Invalid media triggers frosted Error Card (no raw QMessageBox crash) and Try Again retries
                player._on_media_status_changed(QMediaPlayer.MediaStatus.InvalidMedia)
                assert not player._error_card.isHidden()
                with patch.object(player, "_load_video") as mock_reload:
                    player._error_card._retry_btn.click()
                    mock_reload.assert_called_once()
            finally:
                player.close()

    def test_auto_hide_and_wake_on_interaction(self, qtbot, vault_with_video):
        _dir, vault, entry, _session = vault_with_video

        with patch.object(QMediaPlayer, "play"):
            player = VideoPlayer(entry.file_id, vault)
            try:
                player.show()
                qtbot.wait(20)
                player._error_card.hide()

                with patch.object(
                    player._player,
                    "playbackState",
                    return_value=QMediaPlayer.PlaybackState.PlayingState,
                ), patch.object(player, "_is_pointer_over_interactive_chrome", return_value=False):
                    assert player._controls_visible is True
                    player._auto_hide_controls()
                    assert player._controls_visible is False
                    qtbot.wait(250)
                    assert player._top_bar.isHidden()
                    assert player._bottom_bar.isHidden()
                    assert player._center_controls.isHidden()

                    # Key press wakes controls smoothly
                    qtbot.keyClick(player, Qt.Key.Key_I)
                    assert player._controls_visible is True
                    assert not player._top_bar.isHidden()
                    assert not player._bottom_bar.isHidden()
            finally:
                player.close()

    def test_responsive_layouts_and_pip_and_fullscreen_no_overlap(self, qtbot, vault_with_video):
        _dir, vault, entry, _session = vault_with_video

        with patch.object(QMediaPlayer, "play"):
            player = VideoPlayer(entry.file_id, vault)
            try:
                player.show()
                qtbot.wait(20)

                for w, h in [(520, 340), (700, 440), (1120, 668), (1600, 960)]:
                    player.resize(w, h)
                    qtbot.wait(15)
                    stage_rect = player._stage.rect()
                    assert player._top_bar.geometry().bottom() < player._bottom_bar.geometry().top()
                    assert stage_rect.contains(player._center_controls.geometry())
                    if not player._right_rail.isHidden():
                        assert not player._right_rail.geometry().intersects(player._center_controls.geometry())

                # Toggle PiP mode
                player._rail_pip_btn.click()
                qtbot.wait(20)
                assert player._is_pip is True
                assert player.width() == 420
                assert player.height() == 250
                assert player._right_rail.isHidden()

                # Esc exits PiP back to normal mode
                qtbot.keyClick(player, Qt.Key.Key_Escape)
                qtbot.wait(20)
                assert player._is_pip is False

                # Double click / F toggles fullscreen cleanly
                qtbot.keyClick(player, Qt.Key.Key_F)
                qtbot.wait(20)
                assert player._is_fullscreen is True
                assert player._video_canvas._corner_radius == 0.0
                qtbot.keyClick(player, Qt.Key.Key_Escape)
                qtbot.wait(20)
                assert player._is_fullscreen is False
                assert player._video_canvas._corner_radius == 20.0
            finally:
                player.close()

    def test_share_popover_export_decrypted_copy(self, qtbot, vault_with_video, tmp_path):
        _dir, vault, entry, _session = vault_with_video
        out_path = tmp_path / "exported_video.mp4"

        with patch.object(QMediaPlayer, "play"):
            player = VideoPlayer(entry.file_id, vault)
            try:
                player.show()
                qtbot.wait(20)

                with patch(
                    "mylocker.app.ui.video_player.QFileDialog.getSaveFileName",
                    return_value=(str(out_path), "All Files (*)"),
                ):
                    player._download_btn.click()
                    qtbot.waitUntil(lambda: out_path.exists() and out_path.stat().st_size > 0, timeout=5000)

                assert out_path.read_bytes().startswith(b"\x00\x00\x00\x18ftypmp42")
            finally:
                player.close()
