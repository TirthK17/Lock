"""
thumbnails.py — Unified image and video thumbnail generation for Personal Vault.

Supports:
  - Plaintext image & video files at import time (worker-thread-safe, zero leftover locks).
  - Decrypted in-memory image bytes during background vault backfill.
  - Shared QImage -> JPEG base64 conversion for in-memory StreamingVaultIODevice
    video frame extraction without writing plaintext temp files to disk.
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, ImageStat

logger = logging.getLogger(__name__)

THUMB_MAX_SIZE = 256
THUMB_JPEG_QUALITY = 76
_CARD_BG_RGB = (15, 19, 32)  # Dark fallback matte for alpha-channel images


def _pil_to_jpeg_b64(img: Image.Image) -> Optional[str]:
    """Normalize EXIF orientation, composite alpha onto card dark bg, resize & JPEG-encode."""
    try:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        img.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), resampling)

        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, _CARD_BG_RGB)
            bg.paste(rgba, mask=rgba.split()[3])
            rgb = bg
        elif img.mode != "RGB":
            rgb = img.convert("RGB")
        else:
            rgb = img

        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=THUMB_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def qimage_to_thumbnail_b64(qimg) -> Optional[str]:
    """Scale a Qt QImage cleanly on CPU memory and encode as JPEG base64."""
    try:
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        from PySide6.QtGui import QColor, QImage, QPainter

        if qimg is None or qimg.isNull() or qimg.width() <= 0 or qimg.height() <= 0:
            return None

        scaled = qimg.scaled(
            THUMB_MAX_SIZE,
            THUMB_MAX_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.hasAlphaChannel():
            canvas = QImage(scaled.size(), QImage.Format.Format_RGB888)
            canvas.fill(QColor(*_CARD_BG_RGB))
            painter = QPainter(canvas)
            painter.drawImage(0, 0, scaled)
            painter.end()
            out_img = canvas
        else:
            out_img = scaled.convertToFormat(QImage.Format.Format_RGB888)

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        try:
            if not out_img.save(buf, "JPG", THUMB_JPEG_QUALITY):
                return None
            raw = bytes(buf.data())
        finally:
            buf.close()

        if not raw:
            return None
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def is_qimage_mostly_black(qimg) -> bool:
    """Quickly test if a sampled QImage frame is near-black (e.g. video fade-in frame)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage

        if qimg is None or qimg.isNull():
            return True
        tiny = qimg.scaled(16, 16, Qt.AspectRatioMode.IgnoreAspectRatio)
        if tiny.format() != QImage.Format.Format_RGB888:
            tiny = tiny.convertToFormat(QImage.Format.Format_RGB888)
        total_luma = 0.0
        n = 0
        for y in range(0, tiny.height(), 2):
            for x in range(0, tiny.width(), 2):
                c = QColor(tiny.pixel(x, y))
                total_luma += (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
                n += 1
        return (total_luma / max(1, n)) < 10.0
    except Exception:
        return False


def _qt_render_svg_bytes_to_b64(svg_bytes: bytes) -> Optional[str]:
    try:
        from PySide6.QtCore import QByteArray, QRectF
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(QByteArray(svg_bytes))
        if not renderer.isValid():
            return None
        sz = renderer.defaultSize()
        w = max(64, sz.width() if sz.isValid() and sz.width() > 0 else THUMB_MAX_SIZE)
        h = max(64, sz.height() if sz.isValid() and sz.height() > 0 else THUMB_MAX_SIZE)
        scale = min(THUMB_MAX_SIZE / w, THUMB_MAX_SIZE / h)
        tw = max(32, int(round(w * scale)))
        th = max(32, int(round(h * scale)))

        img = QImage(tw, th, QImage.Format.Format_RGB888)
        img.fill(QColor(*_CARD_BG_RGB))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, tw, th))
        painter.end()
        return qimage_to_thumbnail_b64(img)
    except Exception:
        return None


def _qt_image_from_bytes_to_b64(data: bytes) -> Optional[str]:
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImageReader

        ba = QByteArray(data)
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        try:
            reader = QImageReader(buf)
            reader.setAutoTransform(True)
            img = reader.read()
        finally:
            buf.close()
        return qimage_to_thumbnail_b64(img)
    except Exception:
        return None


def _qt_image_from_path_to_b64(path: Path) -> Optional[str]:
    try:
        from PySide6.QtGui import QImageReader

        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        img = reader.read()
        return qimage_to_thumbnail_b64(img)
    except Exception:
        return None


def make_image_thumbnail_b64(source_path: Path) -> Optional[str]:
    """Generate a JPEG base64 thumbnail from a plaintext image file on disk."""
    ext = source_path.suffix.lower()
    if ext == ".svg":
        try:
            return _qt_render_svg_bytes_to_b64(source_path.read_bytes())
        except Exception:
            pass

    # Primary: fast Pillow decode (JPEG draft + EXIF transpose)
    try:
        with Image.open(source_path) as img:
            try:
                img.draft("RGB", (512, 512))
            except Exception:
                pass
            b64 = _pil_to_jpeg_b64(img)
            if b64:
                return b64
    except Exception:
        pass

    # Fallback 1: Qt QImageReader (handles SVG, ICO, TGA, extra formats)
    b64 = _qt_image_from_path_to_b64(source_path)
    if b64:
        return b64

    # Fallback 2: OpenCV image decode if available
    try:
        import cv2
        arr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if arr is not None and arr.size > 0:
            rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            return _pil_to_jpeg_b64(Image.fromarray(rgb))
    except Exception:
        pass

    # Fallback 3: system ffmpeg for exotic formats (HEIC, AVIF, TIFF, WebP, etc.)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(  # nosec B603
                [
                    ffmpeg_bin,
                    "-v",
                    "error",
                    "-i",
                    str(source_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={THUMB_MAX_SIZE}:{THUMB_MAX_SIZE}:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-",
                ],
                capture_output=True,
                timeout=4,
                creationflags=flags,
            )
            if res.returncode == 0 and res.stdout:
                return _qt_image_from_bytes_to_b64(res.stdout) or _pil_to_jpeg_b64(Image.open(io.BytesIO(res.stdout)))
        except Exception:
            pass

    return None


def make_image_thumbnail_from_bytes_b64(
    data: bytes | bytearray, filename_hint: str = ""
) -> Optional[str]:
    """Generate a JPEG base64 thumbnail purely from in-memory image bytes."""
    if not data:
        return None
    raw = bytes(data)
    ext = Path(filename_hint).suffix.lower() if filename_hint else ""
    if ext == ".svg" or (raw.lstrip()[:5].startswith(b"<svg") or b"<svg" in raw[:256].lower()):
        b64 = _qt_render_svg_bytes_to_b64(raw)
        if b64:
            return b64

    try:
        with Image.open(io.BytesIO(raw)) as img:
            try:
                img.draft("RGB", (512, 512))
            except Exception:
                pass
            b64 = _pil_to_jpeg_b64(img)
            if b64:
                return b64
    except Exception:
        pass

    return _qt_image_from_bytes_to_b64(raw)


def _bgr_frame_mean_luma(frame) -> float:
    """Estimate average brightness of a BGR cv2 frame without importing numpy."""
    try:
        pil_tiny = Image.fromarray(frame[:, :, ::-1])
        pil_tiny.thumbnail((32, 32))
        return float(ImageStat.Stat(pil_tiny.convert("L")).mean[0])
    except Exception:
        return 128.0


def make_video_thumbnail_b64(source_path: Path) -> Optional[str]:
    """Extract a representative video frame thumbnail from *source_path*.

    Releases all file handles cleanly before returning so subsequent secure deletion
    never hits WinError 32 sharing violations on Windows.
    """
    # Primary: OpenCV VideoCapture (fast ~10-25ms, worker-thread safe)
    try:
        import cv2

        cap = cv2.VideoCapture(str(source_path))
        try:
            if cap.isOpened():
                total_frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
                best_frame = None

                # Try seeking past intro black frame (min(10% duration, ~1.5s))
                if total_frames > 12:
                    seek_idx = int(min(total_frames * 0.12, fps * 1.5 if fps > 0 else 24))
                    if seek_idx > 0:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, seek_idx)
                        ok, frame = cap.read()
                        if ok and frame is not None and frame.size > 0:
                            if _bgr_frame_mean_luma(frame) >= 10.0:
                                best_frame = frame
                            else:
                                best_frame = frame

                # Fallback: sample from start if seek yielded nothing or dark frame
                if best_frame is None or _bgr_frame_mean_luma(best_frame) < 10.0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    for _probe in range(16):
                        ok, frame = cap.read()
                        if not ok or frame is None or frame.size == 0:
                            break
                        if best_frame is None:
                            best_frame = frame
                        if _bgr_frame_mean_luma(frame) >= 10.0:
                            best_frame = frame
                            break

                if best_frame is not None:
                    rgb = cv2.cvtColor(best_frame, cv2.COLOR_BGR2RGB)
                    b64 = _pil_to_jpeg_b64(Image.fromarray(rgb))
                    if b64:
                        return b64
        finally:
            cap.release()
    except Exception:
        pass

    # Fallback: system ffmpeg CLI if installed on PATH
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for ss_opt in (["-ss", "0.1"], []):
            try:
                res = subprocess.run(  # nosec B603
                    [
                        ffmpeg_bin,
                        "-v",
                        "error",
                        "-i",
                        str(source_path),
                        *ss_opt,
                        "-frames:v",
                        "1",
                        "-vf",
                        f"scale={THUMB_MAX_SIZE}:{THUMB_MAX_SIZE}:force_original_aspect_ratio=decrease",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "mjpeg",
                        "-",
                    ],
                    capture_output=True,
                    timeout=4,
                    creationflags=flags,
                )
                if res.returncode == 0 and res.stdout:
                    b64 = make_image_thumbnail_from_bytes_b64(res.stdout, ".jpg")
                    if b64:
                        return b64
            except Exception:
                pass

    return None


def make_video_thumbnail_from_bytes_b64(
    data: bytes | bytearray, filename_hint: str = ""
) -> Optional[str]:
    """Extract a representative video frame thumbnail from in-memory video bytes."""
    if not data:
        return None
    raw = bytes(data)

    # 1. Pipe directly into ffmpeg stdin (fastest, zero disk I/O, secure in memory)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for ss_opt in (["-ss", "0.1"], []):
            try:
                cmd = [
                    ffmpeg_bin,
                    "-v",
                    "error",
                    *ss_opt,
                    "-i",
                    "pipe:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={THUMB_MAX_SIZE}:{THUMB_MAX_SIZE}:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-",
                ]
                res = subprocess.run(  # nosec B603
                    cmd,
                    input=raw,
                    capture_output=True,
                    timeout=5,
                    creationflags=flags,
                )
                if res.returncode == 0 and res.stdout:
                    b64 = make_image_thumbnail_from_bytes_b64(res.stdout, ".jpg")
                    if b64:
                        return b64
            except Exception:
                pass

    # 2. Fallback: secure temporary file for OpenCV VideoCapture / ffmpeg
    import os
    import tempfile

    ext = Path(filename_hint).suffix.lower() if filename_hint else ".mp4"
    if not ext or len(ext) > 6:
        ext = ".mp4"
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
            tf.write(raw)
            tmp_path = Path(tf.name)
        b64 = make_video_thumbnail_b64(tmp_path)
        if b64:
            return b64
    except Exception:
        pass
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                # Overwrite initial chunk and delete immediately
                tmp_path.write_bytes(b"\x00" * min(len(raw), 4096))
                os.unlink(tmp_path)
            except Exception:
                pass

    return None


def make_media_thumbnail_b64(source_path: Path, mime_type: str) -> Optional[str]:
    """Dispatch thumbnail generation by MIME type for images and videos."""
    m = (mime_type or "").lower()
    if m.startswith("image/"):
        return make_image_thumbnail_b64(source_path)
    if m.startswith("video/"):
        return make_video_thumbnail_b64(source_path)
    return None


__all__ = [
    "THUMB_MAX_SIZE",
    "is_qimage_mostly_black",
    "make_image_thumbnail_b64",
    "make_image_thumbnail_from_bytes_b64",
    "make_media_thumbnail_b64",
    "make_video_thumbnail_b64",
    "make_video_thumbnail_from_bytes_b64",
    "qimage_to_thumbnail_b64",
]
