"""
test_batch_performance_and_multiselect.py — Regression tests for batched vault
ingestion/deletion, crash safety during batched index commits, throttled
progress reporting, incremental Dashboard card rendering, zero-rebuild resize,
and multi-select batch deletion UI.
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

import pytest
from PySide6.QtCore import QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.service import VaultService
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault, VaultError
from mylocker.app.ui.dashboard import _GRID_BATCH_SIZE, Dashboard


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


def _make_png_bytes(width: int = 40, height: int = 40) -> bytes:
    from PySide6.QtCore import QBuffer
    img = QImage(width, height, QImage.Format.Format_RGBA8888)
    img.fill(0xFF4488CC)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


@pytest.fixture
def vault_env(tmp_path):
    get_session().lock()
    vault_dir = tmp_path / "BatchVault"
    ks = Keystore(vault_dir)
    mk, _ = ks.setup("BatchVaultPass#2026", benchmark=False, kdf_params=_fast_kdf())
    session = get_session()
    session.unlock(mk, is_decoy=False)

    vault = Vault(vault_dir)
    vault.initialise()
    ks.config.load()
    sk = make_lockout_signing_key(ks.config.kdf_params.salt)
    lockout = LockoutManager(vault_dir, sk)
    settings_store = SettingsStore(vault_dir, sk)

    yield vault_dir, vault, session, ks, lockout, settings_store
    session.lock()


class TestBatchedVaultOperations:
    def test_add_many_mapped_batches_index_writes(self, vault_env, tmp_path):
        vault_dir, vault, _, _, _, _ = vault_env
        src_dir = tmp_path / "batch_src"
        src_dir.mkdir()

        png_bytes = _make_png_bytes()
        items: list[tuple] = []
        for i in range(20):
            p = src_dir / f"img_{i:02d}.png"
            p.write_bytes(png_bytes + f"-{i}".encode())
            items.append((p, "/"))

        save_calls = 0
        orig_save = vault._save_index

        def _counting_save(mk=None):
            nonlocal save_calls
            save_calls += 1
            return orig_save(mk)

        with patch.object(vault, "_save_index", side_effect=_counting_save):
            entries = vault.add_many_mapped(items, flush_every=16)

        assert len(entries) == 20
        # 20 items flushed in chunks of 16 -> 2 index saves (16 + 4), not 20
        assert save_calls == 2
        assert all(not p.exists() for p, _ in items)
        assert len(vault.refresh().all_files()) == 20
        assert all(e.thumbnail_b64 for e in entries)
        assert not vault._journal.get_pending()
        assert not list((vault.blobs_dir).glob("*.part"))

    def test_add_many_mapped_index_failure_preserves_all_originals(self, vault_env, tmp_path):
        vault_dir, vault, _, _, _, _ = vault_env
        src_dir = tmp_path / "fail_src"
        src_dir.mkdir()

        sources = []
        for i in range(5):
            p = src_dir / f"doc_{i}.txt"
            p.write_text(f"important-content-{i}")
            sources.append(p)

        with patch.object(vault, "_save_index", side_effect=OSError("Simulated disk full")):
            with pytest.raises(VaultError):
                vault.add_many_mapped([(p, "/") for p in sources], flush_every=16)

        # Crash-safety guarantee: NONE of the originals may be deleted when index save fails
        assert all(p.exists() for p in sources)
        assert vault.refresh().all_files() == []
        assert not vault._journal.get_pending()
        assert list(vault.blobs_dir.iterdir()) == []

    def test_add_many_mapped_cancellation_preserves_unstarted_files(self, vault_env, tmp_path):
        vault_dir, vault, _, _, _, _ = vault_env
        src_dir = tmp_path / "cancel_src"
        src_dir.mkdir()

        sources = []
        for i in range(6):
            p = src_dir / f"item_{i}.bin"
            p.write_bytes(os.urandom(4096))
            sources.append(p)

        cancel_evt = threading.Event()

        def _progress(done: int, total: int, label: str) -> None:
            if "Prepared item_1.bin" in label:
                cancel_evt.set()

        entries = vault.add_many_mapped(
            [(p, "/") for p in sources],
            progress=_progress,
            cancel_event=cancel_evt,
            flush_every=16,
        )

        # First 2 items completed before cancel; remaining 4 stayed safe on disk
        assert len(entries) == 2
        assert not sources[0].exists()
        assert not sources[1].exists()
        assert all(p.exists() for p in sources[2:])
        assert len(vault.refresh().all_files()) == 2
        assert not vault._journal.get_pending()

    def test_add_folder_nested_uses_batched_commit_and_cleans_empty_dirs(self, vault_env, tmp_path):
        _, vault, _, _, _, _ = vault_env
        root = tmp_path / "TripPhotos"
        sub = root / "Day1" / "Raw"
        sub.mkdir(parents=True)
        (root / "cover.png").write_bytes(_make_png_bytes())
        (sub / "shot1.png").write_bytes(_make_png_bytes())
        (sub / "notes.txt").write_text("day 1 notes")

        entries = vault.add_folder(root)
        assert len(entries) == 3
        vpaths = sorted(e.vault_path for e in entries)
        assert vpaths == [
            "/TripPhotos/Day1/Raw/notes.txt",
            "/TripPhotos/Day1/Raw/shot1.png",
            "/TripPhotos/cover.png",
        ]
        assert not root.exists()

    def test_delete_many_from_vault_uses_single_index_save(self, vault_env, tmp_path):
        _, vault, _, _, _, _ = vault_env
        src_dir = tmp_path / "del_src"
        src_dir.mkdir()

        items = []
        for i in range(10):
            p = src_dir / f"file_{i}.txt"
            p.write_text(f"payload-{i}")
            items.append((p, "/"))

        entries = vault.add_many_mapped(items, flush_every=16)
        keep_ids = {e.file_id for e in entries[:3]}
        del_ids = [e.file_id for e in entries[3:]]
        del_blobs = [vault.blobs_dir / e.blob_name for e in entries[3:]]
        keep_blobs = [vault.blobs_dir / e.blob_name for e in entries[:3]]

        save_calls = 0
        orig_save = vault._save_index

        def _counting_save(mk=None):
            nonlocal save_calls
            save_calls += 1
            return orig_save(mk)

        with patch.object(vault, "_save_index", side_effect=_counting_save):
            removed = vault.delete_many_from_vault(del_ids)

        assert removed == 7
        assert save_calls == 1
        assert {e.file_id for e in vault.refresh().all_files()} == keep_ids
        assert all(not b.exists() for b in del_blobs)
        assert all(b.exists() for b in keep_blobs)

    def test_vault_service_add_batch_and_delete_files_end_to_end(self, qtbot, vault_env, tmp_path):
        _, vault, _, _, _, _ = vault_env
        folder = tmp_path / "DropFolder"
        folder.mkdir()
        for i in range(6):
            (folder / f"p_{i}.png").write_bytes(_make_png_bytes())
        standalone = tmp_path / "solo.txt"
        standalone.write_text("solo document")

        svc = VaultService(vault)
        finished: list[tuple[str, bool, str]] = []
        svc.task_finished.connect(lambda tid, ok, msg: finished.append((tid, ok, msg)))

        try:
            svc.add_batch([folder, standalone])
            qtbot.waitUntil(lambda: len(finished) == 1, timeout=10000)
            assert finished[0][1] is True

            all_entries = vault.refresh().all_files()
            assert len(all_entries) == 7

            to_delete = [e.file_id for e in all_entries[:4]]
            svc.delete_files(to_delete)
            qtbot.waitUntil(lambda: len(finished) == 2, timeout=10000)
            assert finished[1][1] is True
            assert len(vault.refresh().all_files()) == 3
        finally:
            svc.shutdown()


class TestDashboardMultiSelectAndGridBatching:
    def test_multiselect_ctrl_shift_select_all_escape_and_batch_delete(self, qtbot, vault_env, tmp_path):
        _, vault, session, ks, lockout, settings_store = vault_env
        src_dir = tmp_path / "ui_src"
        src_dir.mkdir()

        items = []
        for i in range(6):
            p = src_dir / f"photo_{i}.png"
            p.write_bytes(_make_png_bytes())
            items.append((p, "/"))
        vault.add_many_mapped(items, flush_every=16)

        dash = Dashboard(
            vault=vault,
            session=session,
            keystore=ks,
            lockout=lockout,
            settings_store=settings_store,
        )
        qtbot.addWidget(dash)
        dash.show()
        dash.load_files(initial=True)

        try:
            assert len(dash._current_entries) == 6
            assert not dash._selection_bar.isVisible()

            # 1. Enter selection mode via topbar Select button
            qtbot.mouseClick(dash._select_btn, Qt.MouseButton.LeftButton)
            assert dash._selection_mode is True
            assert dash._selection_bar.isVisible()
            assert dash._sel_delete_btn.isEnabled() is False

            # 2. Shift-range selection across cards 1..3
            c1 = dash._cards_by_id[dash._current_entries[1].file_id]
            c3 = dash._cards_by_id[dash._current_entries[3].file_id]
            qtbot.mouseClick(c1, Qt.MouseButton.LeftButton)
            qtbot.mouseClick(c3, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
            assert dash._selected_ids == {
                dash._current_entries[1].file_id,
                dash._current_entries[2].file_id,
                dash._current_entries[3].file_id,
            }
            assert dash._sel_delete_btn.isEnabled() is True
            assert "3" in dash._sel_count_lbl.text()

            # 3. Escape clears selection & exits mode
            dash._on_escape_shortcut()
            assert dash._selection_mode is False
            assert dash._selected_ids == set()
            assert not dash._selection_bar.isVisible()

            # 4. Corner badge toggles selection directly & Ctrl+Click adds another
            c0 = dash._cards_by_id[dash._current_entries[0].file_id]
            c5 = dash._cards_by_id[dash._current_entries[5].file_id]
            qtbot.mouseClick(c0._select_btn, Qt.MouseButton.LeftButton)
            assert dash._selection_mode is True
            assert dash._selected_ids == {dash._current_entries[0].file_id}

            qtbot.mouseClick(c5, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier)
            assert dash._selected_ids == {
                dash._current_entries[0].file_id,
                dash._current_entries[5].file_id,
            }

            # 5. Select All button selects all visible items, then toggle one off
            qtbot.mouseClick(dash._sel_all_btn, Qt.MouseButton.LeftButton)
            assert len(dash._selected_ids) == 6
            qtbot.mouseClick(c5._select_btn, Qt.MouseButton.LeftButton)
            assert len(dash._selected_ids) == 5
            surviving_id = dash._current_entries[5].file_id

            # 6. Confirmed batch delete removes the 5 selected files cleanly
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                qtbot.mouseClick(dash._sel_delete_btn, Qt.MouseButton.LeftButton)

            qtbot.waitUntil(lambda: len(vault.refresh().all_files()) == 1, timeout=10000)
            qtbot.waitUntil(lambda: len(dash._current_entries) == 1, timeout=5000)
            assert dash._current_entries[0].file_id == surviving_id
            assert dash._selection_mode is False
            assert dash._selected_ids == set()
        finally:
            dash.cleanup()

    def test_incremental_grid_batch_rendering_and_zero_rebuild_resize(self, qtbot, vault_env, tmp_path):
        _, vault, session, ks, lockout, settings_store = vault_env
        src_dir = tmp_path / "many_src"
        src_dir.mkdir()

        total_files = _GRID_BATCH_SIZE + 18
        items = []
        for i in range(total_files):
            p = src_dir / f"doc_{i:03d}.txt"
            p.write_text(f"payload-{i}")
            items.append((p, "/"))
        vault.add_many_mapped(items, flush_every=32)

        dash = Dashboard(
            vault=vault,
            session=session,
            keystore=ks,
            lockout=lockout,
            settings_store=settings_store,
        )
        qtbot.addWidget(dash)
        dash.show()

        t0 = time.perf_counter()
        dash.load_files(initial=True)
        sync_dt = time.perf_counter() - t0

        try:
            # First chunk renders synchronously; remaining stream on 0ms timer
            assert len(dash._rendered_cards) <= _GRID_BATCH_SIZE
            assert sync_dt < 1.5

            qtbot.waitUntil(lambda: len(dash._rendered_cards) == total_files, timeout=5000)
            first_card_before = dash._rendered_cards[0]
            last_card_before = dash._rendered_cards[-1]

            # Changing column count repositions existing FileCard widgets without recreating them
            old_cols = dash._current_cols
            new_cols = old_cols + 1 if old_cols < 8 else old_cols - 1
            dash._reposition_rendered_cards(new_cols)
            assert dash._current_cols == new_cols
            assert dash._rendered_cards[0] is first_card_before
            assert dash._rendered_cards[-1] is last_card_before
        finally:
            dash.cleanup()


class TestImageAndVideoThumbnails:
    def test_image_thumbnails_handle_exif_transparency_and_svg(self, vault_env, tmp_path):
        import base64
        import io

        from PIL import Image

        _, vault, _, _, _, _ = vault_env
        src_dir = tmp_path / "img_variants"
        src_dir.mkdir()

        # 1. EXIF rotated JPEG (400x200 rotated 90deg -> portrait thumbnail width < height)
        exif_jpg = src_dir / "rotated.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (400, 200), (210, 90, 40)).save(exif_jpg, format="JPEG", exif=exif.tobytes())

        # 2. RGBA transparent PNG
        rgba_png = src_dir / "alpha.png"
        Image.new("RGBA", (180, 120), (40, 180, 220, 160)).save(rgba_png, format="PNG")

        # 3. SVG vector image
        svg_file = src_dir / "vector.svg"
        svg_file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
            '<rect width="120" height="80" fill="#4f6df5"/>'
            '<circle cx="60" cy="40" r="24" fill="#ffffff"/>'
            "</svg>",
            encoding="utf-8",
        )

        entries = vault.add_many_mapped(
            [(exif_jpg, "/"), (rgba_png, "/"), (svg_file, "/")],
            flush_every=16,
        )
        assert len(entries) == 3
        by_name = {e.original_name: e for e in entries}

        rot_raw = base64.b64decode(by_name["rotated.jpg"].thumbnail_b64)
        with Image.open(io.BytesIO(rot_raw)) as rot_im:
            assert rot_im.height > rot_im.width

        assert by_name["alpha.png"].thumbnail_b64
        svg_raw = base64.b64decode(by_name["vector.svg"].thumbnail_b64)
        with Image.open(io.BytesIO(svg_raw)) as svg_im:
            assert svg_im.width > 0 and svg_im.height > 0

    def test_video_thumbnail_generated_on_upload_and_backfilled_in_memory(self, qtbot, vault_env, tmp_path):
        import base64
        import io

        cv2 = pytest.importorskip("cv2")
        np = pytest.importorskip("numpy")
        from PIL import Image, ImageStat

        vault_dir, vault, session, ks, lockout, settings_store = vault_env

        def _make_sample_mp4(path):
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (320, 240))
            try:
                # 5 black intro frames followed by bright teal/amber scene frames
                for _ in range(5):
                    writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
                for _ in range(35):
                    writer.write(np.full((240, 320, 3), (40, 180, 230), dtype=np.uint8))
            finally:
                writer.release()

        src_dir = tmp_path / "vid_src"
        src_dir.mkdir()
        vid_upload = src_dir / "clip_new.mp4"
        vid_legacy = src_dir / "clip_legacy.mp4"
        img_legacy = src_dir / "photo_legacy.png"
        _make_sample_mp4(vid_upload)
        _make_sample_mp4(vid_legacy)
        img_legacy.write_bytes(_make_png_bytes(160, 120))

        # 1. Uploading video generates a real non-black frame thumbnail & releases file lock cleanly
        new_entry = vault.add_file(vid_upload)
        assert not vid_upload.exists()
        assert new_entry.thumbnail_b64
        with Image.open(io.BytesIO(base64.b64decode(new_entry.thumbnail_b64))) as thumb_im:
            assert ImageStat.Stat(thumb_im.convert("L")).mean[0] > 40.0

        # 2. Simulate older vault files uploaded without cached thumbnails
        legacy_entries = vault.add_many_mapped(
            [(vid_legacy, "/"), (img_legacy, "/")],
            generate_thumbnail=False,
            flush_every=16,
        )
        assert all(e.thumbnail_b64 is None for e in legacy_entries)
        legacy_vid_id = legacy_entries[0].file_id
        legacy_img_id = legacy_entries[1].file_id

        dash = Dashboard(
            vault=vault,
            session=session,
            keystore=ks,
            lockout=lockout,
            settings_store=settings_store,
        )
        qtbot.addWidget(dash)
        dash.show()
        dash.load_files(initial=True)

        try:
            # New video card displays play badge right away; legacy cards backfill in memory
            assert not dash._cards_by_id[new_entry.file_id]._video_play_badge.isHidden()

            qtbot.waitUntil(
                lambda: bool(vault._index.index.get(legacy_img_id).thumbnail_b64)
                and bool(vault._index.index.get(legacy_vid_id).thumbnail_b64),
                timeout=10000,
            )

            # Live video card updates with actual frame thumbnail + play badge, persisted to index.dat
            assert not dash._cards_by_id[legacy_vid_id]._video_play_badge.isHidden()
            qtbot.waitUntil(lambda: not dash._pending_thumb_saves, timeout=5000)

            reloaded = vault._index.load(session.get_master_key())
            assert reloaded.get(legacy_img_id).thumbnail_b64
            assert reloaded.get(legacy_vid_id).thumbnail_b64

            # Ensure zero plaintext temp files created inside vault_dir
            assert not [p for p in vault_dir.rglob("*") if p.suffix.lower() in (".mp4", ".png", ".jpg")]
        finally:
            dash.cleanup()


