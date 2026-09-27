#!/usr/bin/env python3
"""
FFMPEG-Forge - a minimal, modern front end for ffmpeg.

Drop files in, pick what you want, press Render. No terminal.

Requires: Python 3.9+, PySide6, and ffmpeg/ffprobe available on PATH
(or pointed at manually in Settings).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "FFMPEG-Forge"
APP_ORG = "FFMPEG-Forge"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# ---------------------------------------------------------------- palette

BG = "#080b1f"
PANEL = "#111936"
PANEL_2 = "#192348"
FIELD = "#0b122c"
BORDER = "#293b70"
BORDER_HI = "#536cc2"
TEXT = "#edf2ff"
MUTED = "#9ca9d3"
DIM = "#65739e"
ACCENT = "#63d8ff"
ACCENT_HI = "#b889ff"
GOOD = "#51e5b0"
BAD = "#ff668f"
WARN = "#ffd166"

MONO = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "monospace")
UIFONT = "Segoe UI" if IS_WIN else ("SF Pro Text" if IS_MAC else "Inter")

VIDEO_EXT = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".ogv", ".mxf", ".ogg",
}
AUDIO_EXT = {
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".opus", ".wma", ".aiff", ".alac",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT

SECTIONS = [("video", "Video"), ("photo", "Photos")]

VIDEO_MODES = [
    ("convert", "Convert"),
    ("compress", "Compress"),
    ("audio", "Extract audio"),
    ("trim", "Trim"),
    ("resize", "Resize"),
    ("gif", "Make GIF"),
    ("frames", "Frames"),
]

PHOTO_MODES = [
    ("photo_convert", "Convert"),
    ("photo_resize", "Resize"),
    ("photo_crop", "Crop"),
    ("photo_adjust", "Adjust"),
]

MODES = VIDEO_MODES + PHOTO_MODES
MODE_SECTION = dict(
    [(k, "video") for k, _ in VIDEO_MODES]
    + [(k, "photo") for k, _ in PHOTO_MODES]
)


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return "video"


def section_accepts(section, kind):
    return kind == "image" if section == "photo" else kind in ("video", "audio")


UPDATE_VERSION_URL = "https://www.gyan.dev/ffmpeg/builds/release-version"
UPDATE_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
UPDATE_SHA_URL = UPDATE_ZIP_URL + ".sha256"
UPDATE_FALLBACK_PAGE = "https://www.ffmpeg.org/download.html"
UA = {"User-Agent": "FFMPEG-Forge/1.0 (+ffmpeg updater)"}


# ---------------------------------------------------------------- helpers


def app_dir() -> Path:
    """Folder the app lives in, whether run as a script or a frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def managed_dir() -> Path:
    """Where FFMPEG-Forge keeps its own private copy of ffmpeg."""
    return app_dir() / "ffmpeg"


def managed_exe() -> Path:
    return managed_dir() / "bin" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")


def no_window():
    """Keep Windows from flashing a console for every subprocess."""
    kw = {}
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def human_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return "?"


def human_time(seconds):
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_time(text):
    """Accept 12, 1:05, 01:02:03.5 or blank. Returns seconds or None."""
    text = (text or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"(\d+:){0,2}\d+(\.\d+)?", text):
        return None
    parts = text.split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def quote(arg):
    if not arg:
        return '""'
    if re.search(r"[\s\"'&|<>^()]", str(arg)):
        return '"' + str(arg).replace('"', r"\"") + '"'
    return str(arg)


def pretty_cmd(cmd):
    return " ".join(quote(c) for c in cmd)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def reveal(path: Path):
    try:
        if IS_WIN:
            os.startfile(str(path))  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# ---------------------------------------------------------------- ffmpeg


class FFmpeg:
    """Locates the binaries and answers questions about them."""

    def __init__(self):
        self.ffmpeg = ""
        self.ffprobe = ""
        self.version = ""
        self.encoders = set()

    def locate(self, manual=""):
        candidates = []
        if manual:
            candidates.append(manual)
        if managed_exe().exists():
            candidates.append(str(managed_exe()))
        found = shutil.which("ffmpeg")
        if found:
            candidates.append(found)
        extra = [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
        ]
        candidates.extend(extra)
        localdata = os.environ.get("LOCALAPPDATA", "")
        if localdata:
            candidates.append(
                str(Path(localdata) / "Microsoft/WinGet/Links/ffmpeg.exe")
            )

        for cand in candidates:
            if not cand:
                continue
            p = Path(cand)
            if p.is_dir():
                p = p / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
            if p.exists() and self._probe_version(str(p)):
                self.ffmpeg = str(p)
                sib = p.with_name("ffprobe.exe" if IS_WIN else "ffprobe")
                self.ffprobe = str(sib) if sib.exists() else (
                    shutil.which("ffprobe") or ""
                )
                self._load_encoders()
                return True
        return False

    def _probe_version(self, exe):
        try:
            out = subprocess.run(
                [exe, "-version"],
                capture_output=True,
                text=True,
                timeout=12,
                **no_window(),
            )
            first = (out.stdout or "").splitlines()
            if first and "ffmpeg version" in first[0]:
                self.version = first[0].split(" Copyright")[0].strip()
                return True
        except Exception:
            return False
        return False

    def _load_encoders(self):
        self.encoders = set()
        try:
            out = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=20,
                **no_window(),
            )
            for line in (out.stdout or "").splitlines():
                m = re.match(r"^\s*[A-Z.]{6}\s+(\S+)", line)
                if m:
                    self.encoders.add(m.group(1))
        except Exception:
            pass

    def has(self, encoder):
        return encoder in self.encoders if self.encoders else False

    @property
    def ready(self):
        return bool(self.ffmpeg)

    @property
    def is_managed(self):
        """True when the active binary is the private copy we may replace."""
        if not self.ffmpeg:
            return False
        try:
            return Path(self.ffmpeg).resolve() == managed_exe().resolve()
        except OSError:
            return False

    @property
    def numeric_version(self):
        return parse_version(self.version)

    def probe(self, path):
        """Returns a MediaInfo, best effort."""
        info = MediaInfo(path=Path(path))
        try:
            info.size = Path(path).stat().st_size
        except OSError:
            pass
        if not self.ffprobe:
            return info
        try:
            out = subprocess.run(
                [
                    self.ffprobe, "-v", "quiet", "-print_format", "json",
                    "-show_format", "-show_streams", str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                **no_window(),
            )
            data = json.loads(out.stdout or "{}")
        except Exception:
            return info

        fmt = data.get("format", {})
        try:
            info.duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            info.duration = None
        try:
            info.bitrate = int(fmt.get("bit_rate"))
        except (TypeError, ValueError):
            info.bitrate = None

        for st in data.get("streams", []):
            kind = st.get("codec_type")
            if kind == "video" and not info.vcodec:
                info.vcodec = st.get("codec_name", "")
                info.width = st.get("width")
                info.height = st.get("height")
                fr = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/0"
                try:
                    num, den = fr.split("/")
                    info.fps = round(float(num) / float(den), 3) if float(den) else None
                except (ValueError, ZeroDivisionError):
                    info.fps = None
                if st.get("disposition", {}).get("attached_pic"):
                    info.vcodec = ""
                    info.width = info.height = None
            elif kind == "audio" and not info.acodec:
                info.acodec = st.get("codec_name", "")
                info.channels = st.get("channels")
                try:
                    info.samplerate = int(st.get("sample_rate"))
                except (TypeError, ValueError):
                    info.samplerate = None
        return info


@dataclass
class MediaInfo:
    path: Path
    duration: float | None = None
    size: int | None = None
    bitrate: int | None = None
    vcodec: str = ""
    acodec: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    channels: int | None = None
    samplerate: int | None = None

    @property
    def has_video(self):
        return bool(self.vcodec)

    @property
    def is_still(self):
        return kind_of(self.path) == "image"

    @property
    def summary(self):
        bits = []
        if self.width and self.height:
            bits.append(f"{self.width}x{self.height}")
        if self.fps and not self.is_still:
            bits.append(f"{self.fps:g} fps")
        if self.duration and not self.is_still:
            bits.append(human_time(self.duration))
        if self.size:
            bits.append(human_size(self.size))
        codecs = "/".join(c for c in (self.vcodec, self.acodec) if c)
        if codecs:
            bits.append(codecs)
        return "  ".join(bits) if bits else "unreadable"


# ---------------------------------------------------------------- job model


@dataclass
class Job:
    info: MediaInfo
    output: Path
    passes: list = field(default_factory=list)   # list[list[str]]
    duration: float | None = None
    status: str = "queued"
    error: str = ""


# ---------------------------------------------------------------- worker


class RenderWorker(QThread):
    file_progress = Signal(int, float)       # index, 0..1
    file_stats = Signal(int, dict)           # index, {eta, size, speed}
    file_status = Signal(int, str, str)      # index, status, message
    total_progress = Signal(float)           # 0..1
    finished_all = Signal(int, int)          # ok, failed

    def __init__(self, ffmpeg_path, jobs, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.jobs = jobs
        self._cancel = False
        self._proc = None

    def cancel(self):
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self):
        ok = failed = 0
        total = len(self.jobs)
        for idx, job in enumerate(self.jobs):
            if self._cancel:
                self.file_status.emit(idx, "cancelled", "")
                continue
            self.file_status.emit(idx, "running", "")
            try:
                job.output.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failed += 1
                self.file_status.emit(idx, "failed", str(exc))
                continue

            npass = len(job.passes)
            success = True
            err = ""
            for pi, cmd in enumerate(job.passes):
                if self._cancel:
                    success = False
                    err = "cancelled"
                    break
                code, err = self._run_one(
                    cmd, job.duration, idx, pi, npass, total
                )
                if code != 0:
                    success = False
                    break

            if self._cancel and not success:
                self.file_status.emit(idx, "cancelled", "")
                try:
                    if job.output.exists() and job.output.stat().st_size == 0:
                        job.output.unlink()
                except OSError:
                    pass
            elif success:
                ok += 1
                self.file_progress.emit(idx, 1.0)
                self.file_status.emit(idx, "done", str(job.output))
            else:
                failed += 1
                self.file_status.emit(idx, "failed", err or "ffmpeg failed")
            self.total_progress.emit((idx + 1) / max(1, total))

        self.finished_all.emit(ok, failed)

    def _run_one(self, cmd, duration, idx, pass_i, npass, total):
        tail = deque(maxlen=25)
        seen = {"size": 0, "speed": 0.0}
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                universal_newlines=True,
                **no_window(),
            )
        except Exception as exc:
            return 1, str(exc)

        proc = self._proc

        def drain_err():
            try:
                for line in proc.stderr:
                    line = line.strip()
                    if line:
                        tail.append(line)
            except Exception:
                pass

        t = threading.Thread(target=drain_err, daemon=True)
        t.start()

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key == "total_size":
                    try:
                        seen["size"] = int(val)
                    except ValueError:
                        pass
                elif key == "speed":
                    m = re.match(r"\s*([\d.]+)x", val)
                    if m:
                        try:
                            seen["speed"] = float(m.group(1))
                        except ValueError:
                            pass
                elif key == "out_time_us" and duration:
                    try:
                        secs = int(val) / 1_000_000.0
                    except ValueError:
                        continue
                    inner = max(0.0, min(1.0, secs / duration))
                    frac = (pass_i + inner) / npass
                    self.file_progress.emit(idx, frac)
                    self.total_progress.emit((idx + frac) / max(1, total))

                    eta = None
                    if seen["speed"] > 0:
                        left = max(0.0, duration - secs)
                        eta = left / seen["speed"]
                        if npass > 1:
                            eta += (npass - 1 - pass_i) * (
                                duration / seen["speed"]
                            )
                    projected = None
                    if seen["size"] and inner > 0.02:
                        projected = int(seen["size"] / inner)
                    self.file_stats.emit(idx, {
                        "eta": eta, "size": seen["size"],
                        "projected": projected, "speed": seen["speed"],
                    })
                elif key == "progress" and val == "end":
                    self.file_progress.emit(idx, (pass_i + 1) / npass)
        except Exception:
            pass

        proc.wait()
        t.join(timeout=2)
        self._proc = None
        return proc.returncode, "\n".join(list(tail)[-6:])


# ---------------------------------------------------------------- widgets


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)


class Combo(QComboBox):
    """Flat combo box painted by hand so it matches the rest of the UI."""

    def __init__(self, items=None, parent=None):
        super().__init__(parent)
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setMinimumHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        if items:
            self.addItems(items)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(BORDER_HI if self.hasFocus() else BORDER), 1))
        p.setBrush(QColor(FIELD))
        p.drawRoundedRect(r, 8, 8)

        text_rect = r.adjusted(12, 0, -30, 0)
        metrics = QFontMetrics(self.font())
        label = metrics.elidedText(
            self.currentText(), Qt.ElideRight, int(text_rect.width())
        )
        p.setPen(QColor(TEXT if self.isEnabled() else DIM))
        p.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), label)

        cx = r.right() - 17
        cy = r.center().y()
        pen = QPen(QColor(MUTED), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 2)
        path.lineTo(cx, cy + 2.5)
        path.lineTo(cx + 4, cy - 2)
        p.drawPath(path)
        p.end()


class Toggle(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._pos

    def set_knob(self, v):
        self._pos = float(v)
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        off = QColor(FIELD)
        on = QColor(ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(QPen(QColor(BORDER if self._pos < 0.5 else ACCENT), 1))
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        d = r.height() - 6
        x = r.left() + 3 + self._pos * (r.width() - d - 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff" if self._pos > 0.5 else MUTED))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        p.end()


class DropZone(QWidget):
    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self.setCursor(Qt.PointingHandCursor)
        self._hot = False

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hot = True
            self.update()

    def dragLeaveEvent(self, e):
        self._hot = False
        self.update()

    def dropEvent(self, e):
        self._hot = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8.5, 8.5, -8.5, -8.5)
        pen = QPen(QColor(ACCENT if self._hot else BORDER_HI), 1.4)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([5, 5])
        p.setPen(pen)
        p.setBrush(QColor("#151827") if self._hot else Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)

        cx = r.center().x()
        cy = r.center().y() - 16
        pen2 = QPen(QColor(ACCENT if self._hot else MUTED), 1.8)
        pen2.setCapStyle(Qt.RoundCap)
        pen2.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen2)
        p.drawLine(int(cx), int(cy - 12), int(cx), int(cy + 8))
        arrow = QPainterPath()
        arrow.moveTo(cx - 7, cy - 5)
        arrow.lineTo(cx, cy - 12)
        arrow.lineTo(cx + 7, cy - 5)
        p.drawPath(arrow)
        p.drawLine(int(cx - 13), int(cy + 14), int(cx + 13), int(cy + 14))

        f = QFont(UIFONT, 10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(
            QRectF(r.left(), cy + 26, r.width(), 22),
            int(Qt.AlignHCenter | Qt.AlignTop),
            "Drop media here",
        )
        p.setFont(QFont(UIFONT, 9))
        p.setPen(QColor(MUTED))
        p.drawText(
            QRectF(r.left(), cy + 48, r.width(), 22),
            int(Qt.AlignHCenter | Qt.AlignTop),
            "or click to browse",
        )
        p.end()


class QueueRow(QFrame):
    remove_requested = Signal(object)

    def __init__(self, info: MediaInfo, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.info = info
        self.kind = kind_of(info.path)
        self.applies = True
        self.status = "queued"
        self.out_path = None
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(12)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(10)
        self.dot.setStyleSheet(f"color:{DIM}; font-size:11px; background:transparent;")
        lay.addWidget(self.dot)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(info.path.name)
        self.name.setStyleSheet(f"color:{TEXT}; font-size:12px; background:transparent;")
        self.meta = QLabel(info.summary)
        self.meta.setStyleSheet(f"color:{MUTED}; font-size:11px; background:transparent;")
        for lb in (self.name, self.meta):
            lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(self.name)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)

        self.bar = QProgressBar()
        self.bar.setObjectName("rowbar")
        self.bar.setFixedWidth(110)
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay.addWidget(self.bar)

        self.state = QLabel("")
        self.state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state.setFixedWidth(78)
        self.state.setStyleSheet(
            f"color:{MUTED}; font-size:11px; background:transparent;"
        )
        lay.addWidget(self.state)

        self.kill = QPushButton("✕")
        self.kill.setObjectName("ghost")
        self.kill.setFixedSize(24, 24)
        self.kill.setCursor(Qt.PointingHandCursor)
        self.kill.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(self.kill)

    def set_progress(self, frac):
        self.bar.show()
        self.bar.setValue(int(frac * 1000))

    def set_applies(self, applies, section="video"):
        self.applies = bool(applies)
        if self.applies:
            self.name.setStyleSheet(
                f"color:{TEXT}; font-size:12px; background:transparent;"
            )
            if self.status == "queued":
                self.state.setText("")
                self.meta.setText(self.info.summary)
                self.meta.setStyleSheet(
                    f"color:{MUTED}; font-size:11px; background:transparent;"
                )
                self.dot.setStyleSheet(
                    f"color:{DIM}; font-size:11px; background:transparent;"
                )
        else:
            self.name.setStyleSheet(
                f"color:{DIM}; font-size:12px; background:transparent;"
            )
            self.meta.setStyleSheet(
                f"color:{DIM}; font-size:11px; background:transparent;"
            )
            self.dot.setStyleSheet(
                f"color:{BORDER_HI}; font-size:11px; background:transparent;"
            )
            self.state.setText("not a photo" if section == "photo"
                               else "not a video")
            self.state.setStyleSheet(
                f"color:{DIM}; font-size:11px; background:transparent;"
            )
            self.bar.hide()

    def set_stats(self, frac, stats):
        bits = ["%d%%" % int(frac * 100)]
        if stats.get("speed"):
            bits.append("%.1fx" % stats["speed"])
        if stats.get("eta") is not None:
            bits.append("%s left" % human_time(stats["eta"]))
        if stats.get("projected"):
            bits.append("~%s" % human_size(stats["projected"]))
        self.meta.setText("   ".join(bits))
        self.meta.setStyleSheet(
            f"color:{MUTED}; font-size:11px; background:transparent;"
        )

    def set_status(self, status, message=""):
        self.status = status
        if status == "queued":
            self.meta.setText(self.info.summary)
            self.meta.setStyleSheet(
                f"color:{MUTED}; font-size:11px; background:transparent;"
            )
            self.bar.hide()
            self.bar.setValue(0)
        colors = {
            "queued": DIM, "running": ACCENT, "done": GOOD,
            "failed": BAD, "cancelled": WARN,
        }
        self.dot.setStyleSheet(
            f"color:{colors.get(status, DIM)}; font-size:11px; background:transparent;"
        )
        labels = {
            "queued": "", "running": "rendering", "done": "done",
            "failed": "failed", "cancelled": "stopped",
        }
        self.state.setText(labels.get(status, ""))
        self.state.setStyleSheet(
            f"color:{colors.get(status, MUTED)}; font-size:11px; background:transparent;"
        )
        if status == "done":
            self.out_path = Path(message) if message else None
            self.bar.setValue(1000)
            self.meta.setText(f"→ {Path(message).name}" if message else "done")
        elif status == "failed" and message:
            self.meta.setText(message.splitlines()[-1][:110])
            self.meta.setStyleSheet(
                f"color:{BAD}; font-size:11px; background:transparent;"
            )


def label(text, color=MUTED, size=11, bold=False):
    lb = QLabel(text)
    weight = "600" if bold else "400"
    lb.setStyleSheet(
        f"color:{color}; font-size:{size}px; font-weight:{weight};"
        "background:transparent;"
    )
    return lb


class Field(QWidget):
    """A labelled control laid out in a consistent column."""

    def __init__(self, text, widget, hint="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(label(text.upper(), MUTED, 10, True))
        lay.addWidget(widget)
        if hint:
            lay.addWidget(label(hint, DIM, 10))
        self.widget = widget


class SliderField(QWidget):
    changed = Signal()

    def __init__(self, text, lo, hi, value, fmt=lambda v: str(v), parent=None):
        super().__init__(parent)
        self._fmt = fmt
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(label(text.upper(), MUTED, 10, True))
        head.addStretch(1)
        self.value_lb = label(fmt(value), TEXT, 11, True)
        head.addWidget(self.value_lb)
        lay.addLayout(head)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider)

    def _on_change(self, v):
        self.value_lb.setText(self._fmt(v))
        self.changed.emit()

    def value(self):
        return self.slider.value()


# ---------------------------------------------------------------- panels


RES_CHOICES = [
    ("Keep original", 0),
    ("2160p (4K)", 2160),
    ("1440p", 1440),
    ("1080p", 1080),
    ("720p", 720),
    ("480p", 480),
    ("360p", 360),
]

FPS_CHOICES = ["Keep original", "60", "50", "30", "25", "24", "15", "12"]

X264_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]


class ModePanel(QWidget):
    changed = Signal()

    def __init__(self, ff: FFmpeg, parent=None):
        super().__init__(parent)
        self.ff = ff
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(13)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(2, 1)
        self._row = 0
        self.build()
        # soak up spare height at the bottom so rows stay tight to the top
        self.grid.setRowStretch(self._row + 1, 1)

    def add(self, widget, col=0, span=1):
        self.grid.addWidget(widget, self._row, col, 1, span)
        if col + span >= 3:
            self._row += 1

    def newline(self):
        self._row += 1

    def wire(self, *widgets):
        for w in widgets:
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.changed)
            elif isinstance(w, SliderField):
                w.changed.connect(self.changed)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(self.changed)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self.changed)
            elif isinstance(w, (Toggle, QCheckBox)):
                w.toggled.connect(self.changed)

    def build(self):
        raise NotImplementedError

    def values(self):
        raise NotImplementedError

    def suffix(self):
        return "_out"

    def extension(self, info):
        return info.path.suffix or ".mp4"


def encoder_choices(ff: FFmpeg):
    out = [("H.264 (x264)", "libx264")]
    if ff.has("h264_nvenc"):
        out.append(("H.264 (NVENC, GPU)", "h264_nvenc"))
    out.append(("H.265 / HEVC (x265)", "libx265"))
    if ff.has("hevc_nvenc"):
        out.append(("H.265 (NVENC, GPU)", "hevc_nvenc"))
    if ff.has("libsvtav1"):
        out.append(("AV1 (SVT)", "libsvtav1"))
    elif ff.has("libaom-av1"):
        out.append(("AV1 (aom)", "libaom-av1"))
    if ff.has("libvpx-vp9"):
        out.append(("VP9", "libvpx-vp9"))
    out.append(("Copy (no re-encode)", "copy"))
    return out


class ConvertPanel(ModePanel):
    def build(self):
        self.container = Combo(["mp4", "mkv", "mov", "webm", "avi"])
        encs = encoder_choices(self.ff)
        self.enc_map = encs
        self.encoder = Combo([e[0] for e in encs])
        self.quality = SliderField(
            "Quality", 14, 34, 21,
            lambda v: f"CRF {v}  ({'visually lossless' if v <= 17 else 'high' if v <= 21 else 'balanced' if v <= 26 else 'small file'})",
        )
        self.preset = Combo(X264_PRESETS)
        self.preset.setCurrentText("medium")
        self.resolution = Combo([r[0] for r in RES_CHOICES])
        self.fps = Combo(FPS_CHOICES)
        self.audio = Combo([
            "AAC 192k", "AAC 128k", "AAC 320k", "MP3 192k",
            "Opus 128k", "Copy original", "Remove audio",
        ])
        self.transform = Combo([
            "None", "Rotate 90° right", "Rotate 90° left",
            "Rotate 180°", "Flip horizontal", "Flip vertical",
        ])
        self.faststart = Toggle(True)

        self.add(Field("Container", self.container), 0)
        self.add(Field("Video encoder", self.encoder), 1)
        self.add(Field("Speed preset", self.preset), 2)
        self.add(Field("Resolution", self.resolution), 0)
        self.add(Field("Frame rate", self.fps), 1)
        self.add(Field("Audio", self.audio), 2)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(self.faststart)
        rl.addWidget(label("Optimize for web playback", TEXT, 11))
        rl.addStretch(1)
        self.add(Field("Transform", self.transform), 0)
        self.add(Field("Streaming", row), 1, 2)
        self.add(self.quality, 0, 3)

        self.container.currentTextChanged.connect(self._sync_container)
        self.wire(
            self.container, self.encoder, self.quality, self.preset,
            self.resolution, self.fps, self.audio, self.transform,
            self.faststart,
        )

    def _sync_container(self, text):
        if text == "webm":
            for i, (name, code) in enumerate(self.enc_map):
                if code in ("libvpx-vp9", "libsvtav1", "libaom-av1"):
                    self.encoder.setCurrentIndex(i)
                    break
            self.audio.setCurrentText("Opus 128k")

    def suffix(self):
        return "_converted"

    def extension(self, info):
        return "." + self.container.currentText()

    def values(self):
        return {
            "container": self.container.currentText(),
            "encoder": self.enc_map[self.encoder.currentIndex()][1],
            "crf": self.quality.value(),
            "preset": self.preset.currentText(),
            "height": RES_CHOICES[self.resolution.currentIndex()][1],
            "fps": self.fps.currentText(),
            "audio": self.audio.currentText(),
            "transform": self.transform.currentText(),
            "faststart": self.faststart.isChecked(),
        }


class CompressPanel(ModePanel):
    def build(self):
        self.strategy = Combo(["Target quality", "Target file size"])
        encs = [e for e in encoder_choices(self.ff) if e[1] != "copy"]
        self.enc_map = encs
        self.encoder = Combo([e[0] for e in encs])
        self.quality = SliderField(
            "Compression", 18, 34, 26,
            lambda v: f"CRF {v}  ({'larger, sharper' if v <= 22 else 'balanced' if v <= 27 else 'small file'})",
        )
        self.target = QSpinBox()
        self.target.setRange(1, 40000)
        self.target.setValue(25)
        self.target.setSuffix("  MB")
        self.target.setMinimumHeight(34)
        self.resolution = Combo([r[0] for r in RES_CHOICES])
        self.audio = Combo(["AAC 128k", "AAC 96k", "AAC 192k", "Remove audio"])

        self.add(Field("Strategy", self.strategy), 0)
        self.add(Field("Encoder", self.encoder), 1)
        self.add(Field("Audio", self.audio), 2)
        self.add(Field("Resolution cap", self.resolution), 0)
        self.size_field = Field("Target size", self.target, "per file")
        self.add(self.size_field, 1)
        self.newline()
        self.add(self.quality, 0, 3)

        self.strategy.currentIndexChanged.connect(self._sync)
        self.wire(
            self.strategy, self.encoder, self.quality, self.target,
            self.resolution, self.audio,
        )
        self._sync()

    def _sync(self):
        by_size = self.strategy.currentIndex() == 1
        self.size_field.setVisible(by_size)
        self.quality.setVisible(not by_size)

    def suffix(self):
        return "_small"

    def extension(self, info):
        return ".mp4"

    def values(self):
        return {
            "by_size": self.strategy.currentIndex() == 1,
            "encoder": self.enc_map[self.encoder.currentIndex()][1],
            "crf": self.quality.value(),
            "target_mb": self.target.value(),
            "height": RES_CHOICES[self.resolution.currentIndex()][1],
            "audio": self.audio.currentText(),
        }


class AudioPanel(ModePanel):
    FORMATS = {
        "MP3": (".mp3", "libmp3lame"),
        "AAC (m4a)": (".m4a", "aac"),
        "WAV (uncompressed)": (".wav", "pcm_s16le"),
        "FLAC (lossless)": (".flac", "flac"),
        "Opus": (".opus", "libopus"),
        "Copy original stream": (".mka", "copy"),
    }

    def build(self):
        self.fmt = Combo(list(self.FORMATS.keys()))
        self.bitrate = Combo(["320k", "256k", "192k", "160k", "128k", "96k"])
        self.bitrate.setCurrentText("192k")
        self.rate = Combo(["Keep original", "48000", "44100", "32000", "22050"])
        self.channels = Combo(["Keep original", "Stereo", "Mono"])
        self.normalize = Toggle(False)

        self.add(Field("Format", self.fmt), 0)
        self.add(Field("Bitrate", self.bitrate), 1)
        self.add(Field("Sample rate", self.rate), 2)
        self.add(Field("Channels", self.channels), 0)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(self.normalize)
        rl.addWidget(label("Normalize loudness (EBU R128)", TEXT, 11))
        rl.addStretch(1)
        self.add(Field("Loudness", row), 1, 2)

        self.fmt.currentTextChanged.connect(self._sync)
        self.wire(
            self.fmt, self.bitrate, self.rate, self.channels, self.normalize
        )
        self._sync()

    def _sync(self):
        codec = self.FORMATS[self.fmt.currentText()][1]
        lossy = codec in ("libmp3lame", "aac", "libopus")
        self.bitrate.setEnabled(lossy)
        self.normalize.setEnabled(codec != "copy")

    def suffix(self):
        return ""

    def extension(self, info):
        return self.FORMATS[self.fmt.currentText()][0]

    def values(self):
        ext, codec = self.FORMATS[self.fmt.currentText()]
        return {
            "ext": ext,
            "codec": codec,
            "bitrate": self.bitrate.currentText(),
            "rate": self.rate.currentText(),
            "channels": self.channels.currentText(),
            "normalize": self.normalize.isChecked(),
        }


class TrimPanel(ModePanel):
    def build(self):
        self.start = QLineEdit()
        self.start.setPlaceholderText("0:00")
        self.start.setMinimumHeight(34)
        self.end = QLineEdit()
        self.end.setPlaceholderText("end of file")
        self.end.setMinimumHeight(34)
        self.method = Combo([
            "Fast (stream copy, no quality loss)",
            "Precise (re-encode, exact frame)",
        ])
        self.quality = SliderField("Quality (precise only)", 14, 30, 20,
                                   lambda v: f"CRF {v}")

        self.add(Field("Start at", self.start, "h:mm:ss or seconds"), 0)
        self.add(Field("End at", self.end, "leave blank for end of file"), 1)
        self.add(Field("Method", self.method), 2)
        self.add(self.quality, 0, 3)

        self.method.currentIndexChanged.connect(self._sync)
        self.wire(self.start, self.end, self.method, self.quality)
        self._sync()

    def _sync(self):
        self.quality.setVisible(self.method.currentIndex() == 1)

    def suffix(self):
        return "_clip"

    def values(self):
        return {
            "start": parse_time(self.start.text()),
            "end": parse_time(self.end.text()),
            "precise": self.method.currentIndex() == 1,
            "crf": self.quality.value(),
        }


class ResizePanel(ModePanel):
    def build(self):
        self.preset = Combo([r[0] for r in RES_CHOICES] + ["Custom width"])
        self.preset.setCurrentIndex(3)
        self.width = QSpinBox()
        self.width.setRange(16, 16384)
        self.width.setValue(1280)
        self.width.setSuffix("  px wide")
        self.width.setMinimumHeight(34)
        self.fit = Combo([
            "Scale (keep aspect ratio)",
            "Pad to fit (letterbox)",
            "Crop to fill",
        ])
        encs = [e for e in encoder_choices(self.ff) if e[1] != "copy"]
        self.enc_map = encs
        self.encoder = Combo([e[0] for e in encs])
        self.quality = SliderField("Quality", 14, 32, 21, lambda v: f"CRF {v}")

        self.add(Field("Size", self.preset), 0)
        self.custom_field = Field("Custom width", self.width, "height auto")
        self.add(self.custom_field, 1)
        self.add(Field("Fit", self.fit), 2)
        self.add(Field("Encoder", self.encoder), 0)
        self.newline()
        self.add(self.quality, 0, 3)

        self.preset.currentIndexChanged.connect(self._sync)
        self.wire(
            self.preset, self.width, self.fit, self.encoder, self.quality
        )
        self._sync()

    def _sync(self):
        self.custom_field.setVisible(
            self.preset.currentIndex() == len(RES_CHOICES)
        )

    def suffix(self):
        return "_resized"

    def extension(self, info):
        return info.path.suffix if info.path.suffix else ".mp4"

    def values(self):
        idx = self.preset.currentIndex()
        return {
            "height": RES_CHOICES[idx][1] if idx < len(RES_CHOICES) else 0,
            "custom_width": self.width.value() if idx == len(RES_CHOICES) else 0,
            "fit": self.fit.currentIndex(),
            "encoder": self.enc_map[self.encoder.currentIndex()][1],
            "crf": self.quality.value(),
        }


class GifPanel(ModePanel):
    def build(self):
        self.fps = Combo(["24", "20", "15", "12", "10", "8"])
        self.fps.setCurrentText("15")
        self.width = QSpinBox()
        self.width.setRange(80, 2000)
        self.width.setValue(480)
        self.width.setSuffix("  px wide")
        self.width.setMinimumHeight(34)
        self.start = QLineEdit()
        self.start.setPlaceholderText("0:00")
        self.start.setMinimumHeight(34)
        self.length = QLineEdit()
        self.length.setPlaceholderText("whole clip")
        self.length.setMinimumHeight(34)
        self.dither = Combo([
            "Smooth (Bayer dither)", "Sharp (no dither)", "Best colors (sierra2)"
        ])
        self.loop = Combo(["Loop forever", "Play once"])

        self.add(Field("Frame rate", self.fps), 0)
        self.add(Field("Width", self.width), 1)
        self.add(Field("Dithering", self.dither), 2)
        self.add(Field("Start at", self.start), 0)
        self.add(Field("Length", self.length, "seconds or h:mm:ss"), 1)
        self.add(Field("Looping", self.loop), 2)

        self.wire(
            self.fps, self.width, self.start, self.length,
            self.dither, self.loop,
        )

    def suffix(self):
        return ""

    def extension(self, info):
        return ".gif"

    def values(self):
        return {
            "fps": int(self.fps.currentText()),
            "width": self.width.value(),
            "start": parse_time(self.start.text()),
            "length": parse_time(self.length.text()),
            "dither": self.dither.currentIndex(),
            "loop": 0 if self.loop.currentIndex() == 0 else 1,
        }


class FramesPanel(ModePanel):
    def build(self):
        self.mode = Combo([
            "Every N seconds", "Fixed number of frames",
            "Single frame at time", "Every frame",
        ])
        self.interval = QSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(5)
        self.interval.setSuffix("  sec")
        self.interval.setMinimumHeight(34)
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(10)
        self.count.setSuffix("  frames")
        self.count.setMinimumHeight(34)
        self.at = QLineEdit()
        self.at.setPlaceholderText("0:05")
        self.at.setMinimumHeight(34)
        self.fmt = Combo(["PNG (lossless)", "JPEG"])
        self.width = QSpinBox()
        self.width.setRange(0, 16384)
        self.width.setValue(0)
        self.width.setSpecialValueText("Original")
        self.width.setSuffix("  px wide")
        self.width.setMinimumHeight(34)

        self.add(Field("Mode", self.mode), 0)
        self.interval_field = Field("Interval", self.interval)
        self.count_field = Field("How many", self.count)
        self.at_field = Field("Timestamp", self.at)
        self.add(self.interval_field, 1)
        self.add(self.count_field, 2)
        self.add(self.at_field, 0)
        self.add(Field("Format", self.fmt), 1)
        self.add(Field("Width", self.width), 2)

        self.mode.currentIndexChanged.connect(self._sync)
        self.wire(
            self.mode, self.interval, self.count, self.at, self.fmt, self.width
        )
        self._sync()

    def _sync(self):
        i = self.mode.currentIndex()
        self.interval_field.setVisible(i == 0)
        self.count_field.setVisible(i == 1)
        self.at_field.setVisible(i == 2)

    def suffix(self):
        return "_frames"

    def extension(self, info):
        return ".png" if self.fmt.currentIndex() == 0 else ".jpg"

    def values(self):
        return {
            "mode": self.mode.currentIndex(),
            "interval": self.interval.value(),
            "count": self.count.value(),
            "at": parse_time(self.at.text()),
            "jpeg": self.fmt.currentIndex() == 1,
            "width": self.width.value(),
        }


# ---------------------------------------------------------------- builder


AUDIO_MAP = {
    "AAC 192k": ("aac", "192k"),
    "AAC 128k": ("aac", "128k"),
    "AAC 96k": ("aac", "96k"),
    "AAC 320k": ("aac", "320k"),
    "MP3 192k": ("libmp3lame", "192k"),
    "Opus 128k": ("libopus", "128k"),
    "Copy original": ("copy", ""),
    "Remove audio": ("none", ""),
}

TRANSFORM_MAP = {
    "None": "",
    "Rotate 90° right": "transpose=1",
    "Rotate 90° left": "transpose=2",
    "Rotate 180°": "transpose=1,transpose=1",
    "Flip horizontal": "hflip",
    "Flip vertical": "vflip",
}


def video_codec_args(encoder, crf, preset="medium"):
    """Translate a quality number into the right knob for each encoder."""
    if encoder == "copy":
        return ["-c:v", "copy"]
    if encoder in ("h264_nvenc", "hevc_nvenc"):
        return [
            "-c:v", encoder, "-preset", "p5", "-rc", "vbr",
            "-cq", str(crf), "-b:v", "0",
        ]
    if encoder == "libsvtav1":
        return ["-c:v", encoder, "-crf", str(crf), "-preset", "6"]
    if encoder == "libaom-av1":
        return ["-c:v", encoder, "-crf", str(crf), "-b:v", "0", "-cpu-used", "5"]
    if encoder == "libvpx-vp9":
        return ["-c:v", encoder, "-crf", str(crf), "-b:v", "0", "-row-mt", "1"]
    return ["-c:v", encoder, "-crf", str(crf), "-preset", preset]


def audio_args(choice):
    codec, bitrate = AUDIO_MAP.get(choice, ("aac", "192k"))
    if codec == "none":
        return ["-an"]
    if codec == "copy":
        return ["-c:a", "copy"]
    args = ["-c:a", codec]
    if bitrate:
        args += ["-b:a", bitrate]
    return args


def scale_filter(height=0, width=0, cap=False):
    """cap=True never enlarges a source that is already smaller."""
    if width:
        if cap:
            return f"scale='min(iw,{width})':-2:flags=lanczos"
        return f"scale={width}:-2:flags=lanczos"
    if height:
        if cap:
            return f"scale=-2:'min(ih,{height})':flags=lanczos"
        return f"scale=-2:{height}:flags=lanczos"
    return ""


BASE = ["-hide_banner", "-loglevel", "error", "-nostdin",
        "-progress", "pipe:1", "-nostats", "-y"]


def build_job(ff: FFmpeg, mode, opts, info: MediaInfo, out_dir: Path,
              suffix: str, ext: str) -> Job:
    """Turn one file plus the current options into a runnable Job."""
    src = str(info.path)
    stem = info.path.stem
    duration = info.duration

    if mode == "frames":
        name = f"{stem}{suffix}"
        if opts["mode"] == 2:
            out = unique_path(out_dir / f"{stem}_frame{ext}")
        else:
            folder = unique_path(out_dir / name)
            out = folder / f"{stem}_%04d{ext}"
    else:
        out = unique_path(out_dir / f"{stem}{suffix}{ext}")

    job = Job(info=info, output=out, duration=duration)
    exe = ff.ffmpeg

    if mode == "convert":
        filters = []
        sf = scale_filter(height=opts["height"])
        if sf:
            filters.append(sf)
        tf = TRANSFORM_MAP.get(opts["transform"], "")
        if tf:
            filters.append(tf)
        cmd = [exe] + BASE + ["-i", src]
        if opts["fps"] != "Keep original":
            cmd += ["-r", opts["fps"]]
        if filters and opts["encoder"] != "copy":
            cmd += ["-vf", ",".join(filters)]
        # Select the primary video stream explicitly. This prevents ffmpeg
        # from producing an empty Matroska/MP4 when audio is set to Remove.
        cmd += ["-map", "0:v:0"]
        if opts["audio"] != "none":
            cmd += ["-map", "0:a:0?"]
        cmd += video_codec_args(opts["encoder"], opts["crf"], opts["preset"])
        cmd += audio_args(opts["audio"])
        if opts["faststart"] and opts["container"] in ("mp4", "mov", "m4v"):
            cmd += ["-movflags", "+faststart"]
        if opts["encoder"] not in ("copy",):
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += [str(out)]
        job.passes = [cmd]

    elif mode == "compress":
        sf = scale_filter(height=opts["height"], cap=True)
        acodec, abitrate = AUDIO_MAP.get(opts["audio"], ("aac", "128k"))
        if opts["by_size"] and duration:
            akbps = 0 if acodec == "none" else int(
                re.sub(r"\D", "", abitrate) or 128
            )
            total_kbit = opts["target_mb"] * 8192
            vkbps = max(80, int(total_kbit / duration) - akbps - 8)
            logdir = Path(tempfile.gettempdir()) / "reel_pass"
            logdir.mkdir(parents=True, exist_ok=True)
            logfile = str(logdir / f"{abs(hash(src)) % 10**8}")
            null = "NUL" if IS_WIN else "/dev/null"

            enc = opts["encoder"]
            if enc in ("h264_nvenc", "hevc_nvenc"):
                venc = ["-c:v", enc, "-preset", "p5", "-b:v", f"{vkbps}k",
                        "-maxrate", f"{int(vkbps * 1.5)}k",
                        "-bufsize", f"{vkbps * 2}k"]
                p2 = [exe] + BASE + ["-i", src]
                if sf:
                    p2 += ["-vf", sf]
                p2 += venc
                p2 += (["-an"] if acodec == "none"
                       else ["-c:a", "aac", "-b:a", abitrate])
                p2 += ["-movflags", "+faststart", str(out)]
                job.passes = [p2]
            else:
                p1 = [exe] + BASE + ["-i", src]
                if sf:
                    p1 += ["-vf", sf]
                p1 += ["-c:v", enc, "-b:v", f"{vkbps}k", "-pass", "1",
                       "-passlogfile", logfile, "-preset", "medium",
                       "-an", "-f", "null", null]
                p2 = [exe] + BASE + ["-i", src]
                if sf:
                    p2 += ["-vf", sf]
                p2 += ["-c:v", enc, "-b:v", f"{vkbps}k", "-pass", "2",
                       "-passlogfile", logfile, "-preset", "medium"]
                p2 += (["-an"] if acodec == "none"
                       else ["-c:a", "aac", "-b:a", abitrate])
                p2 += ["-pix_fmt", "yuv420p", "-movflags", "+faststart",
                       str(out)]
                job.passes = [p1, p2]
        else:
            cmd = [exe] + BASE + ["-i", src]
            if sf:
                cmd += ["-vf", sf]
            cmd += video_codec_args(opts["encoder"], opts["crf"], "slow")
            cmd += (["-an"] if acodec == "none"
                    else ["-c:a", "aac", "-b:a", abitrate or "128k"])
            cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            job.passes = [cmd]

    elif mode == "audio":
        cmd = [exe] + BASE + ["-i", src, "-vn"]
        if opts["normalize"] and opts["codec"] != "copy":
            cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        cmd += ["-c:a", opts["codec"]]
        if opts["codec"] in ("libmp3lame", "aac", "libopus"):
            cmd += ["-b:a", opts["bitrate"]]
        if opts["rate"] != "Keep original":
            cmd += ["-ar", opts["rate"]]
        if opts["channels"] == "Mono":
            cmd += ["-ac", "1"]
        elif opts["channels"] == "Stereo":
            cmd += ["-ac", "2"]
        cmd += [str(out)]
        job.passes = [cmd]

    elif mode == "trim":
        start = opts["start"] or 0
        end = opts["end"]
        cmd = [exe] + BASE
        if opts["precise"]:
            cmd += ["-ss", f"{start:.3f}", "-i", src]
            if end is not None:
                cmd += ["-t", f"{max(0.01, end - start):.3f}"]
            cmd += video_codec_args("libx264", opts["crf"], "medium")
            cmd += ["-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-ss", f"{start:.3f}", "-i", src]
            if end is not None:
                cmd += ["-t", f"{max(0.01, end - start):.3f}"]
            cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        cmd += [str(out)]
        job.passes = [cmd]
        if end is not None:
            job.duration = max(0.01, end - start)
        elif duration:
            job.duration = max(0.01, duration - start)

    elif mode == "resize":
        if opts["custom_width"]:
            target_w, target_h = opts["custom_width"], 0
        else:
            target_w, target_h = 0, opts["height"]
        if opts["fit"] == 0 or not target_h:
            vf = scale_filter(height=target_h, width=target_w) or "null"
        else:
            w = int(target_h * 16 / 9) // 2 * 2
            if opts["fit"] == 1:
                vf = (f"scale={w}:{target_h}:force_original_aspect_ratio="
                      f"decrease,pad={w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black")
            else:
                vf = (f"scale={w}:{target_h}:force_original_aspect_ratio="
                      f"increase,crop={w}:{target_h}")
        cmd = [exe] + BASE + ["-i", src, "-vf", vf]
        cmd += video_codec_args(opts["encoder"], opts["crf"], "medium")
        cmd += ["-c:a", "copy", "-pix_fmt", "yuv420p", str(out)]
        job.passes = [cmd]

    elif mode == "gif":
        dither = {
            0: "dither=bayer:bayer_scale=5",
            1: "dither=none",
            2: "dither=sierra2_4a",
        }[opts["dither"]]
        vf = (
            f"fps={opts['fps']},scale={opts['width']}:-1:flags=lanczos,"
            f"split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
            f"[s1][p]paletteuse={dither}"
        )
        cmd = [exe] + BASE
        if opts["start"]:
            cmd += ["-ss", f"{opts['start']:.3f}"]
        cmd += ["-i", src]
        if opts["length"]:
            cmd += ["-t", f"{opts['length']:.3f}"]
        cmd += ["-filter_complex", vf, "-loop", str(opts["loop"]), str(out)]
        job.passes = [cmd]
        span = opts["length"] or (
            (duration - (opts["start"] or 0)) if duration else None
        )
        job.duration = span

    elif mode.startswith("photo_"):
        cmd = [exe] + BASE + ["-i", src]
        chain = photo_filters(mode, opts, info)
        if chain:
            cmd += ["-vf", ",".join(chain)]
        cmd += ["-frames:v", "1"]
        cmd += image_codec_args(
            ext, opts.get("quality", 90),
            opts.get("lossless", False), opts.get("strip", False),
        )
        cmd += [str(out)]
        job.passes = [cmd]
        job.duration = None

    elif mode == "frames":
        cmd = [exe] + BASE
        m = opts["mode"]
        if m == 2:
            cmd += ["-ss", f"{opts['at'] or 0:.3f}", "-i", src,
                    "-frames:v", "1"]
        else:
            cmd += ["-i", src]
        filters = []
        if m == 0:
            filters.append(f"fps=1/{opts['interval']}")
        elif m == 1 and duration:
            filters.append(f"fps={opts['count'] / max(0.1, duration):.6f}")
        if opts["width"]:
            filters.append(f"scale={opts['width']}:-2:flags=lanczos")
        if filters:
            cmd += ["-vf", ",".join(filters)]
        if opts["jpeg"]:
            cmd += ["-q:v", "2"]
        cmd += ["-vsync", "vfr" if m in (0, 1) else "0", str(out)]
        job.passes = [cmd]

    return job


# ---------------------------------------------------------------- estimates


CODEC_WEIGHT = {
    "libx264": 1.0,
    "h264_nvenc": 1.45,
    "libx265": 0.62,
    "hevc_nvenc": 0.85,
    "libsvtav1": 0.55,
    "libaom-av1": 0.52,
    "libvpx-vp9": 0.70,
}


def video_bits_per_second(encoder, crf, width, height, fps):
    """Rough bitrate for a CRF encode. Content varies wildly, so this is
    a ballpark, not a promise."""
    if not width or not height:
        return None
    fps = fps or 30.0
    bpp = 0.08 * (2 ** ((23 - crf) / 6.0)) * CODEC_WEIGHT.get(encoder, 1.0)
    return width * height * fps * bpp


def target_dimensions(info, height=0, width=0):
    if not info.width or not info.height:
        return (None, None)
    if height:
        scale = height / float(info.height)
        return (int(info.width * scale) // 2 * 2, height)
    if width:
        scale = width / float(info.width)
        return (width, int(info.height * scale) // 2 * 2)
    return (info.width, info.height)


def audio_bits_per_second(choice):
    codec, bitrate = AUDIO_MAP.get(choice, ("aac", "128k"))
    if codec == "none":
        return 0
    if codec == "copy":
        return 128_000
    digits = re.sub(r"\D", "", bitrate or "128")
    return int(digits or 128) * 1000


def estimate_bytes(mode, opts, info: MediaInfo):
    """Predicted output size in bytes, or None when a guess would mislead."""
    dur = info.duration
    try:
        if mode == "convert":
            if opts["encoder"] == "copy":
                return info.size
            if not dur:
                return None
            w, h = target_dimensions(info, height=opts["height"])
            fps = info.fps
            if opts["fps"] != "Keep original":
                try:
                    fps = float(opts["fps"])
                except ValueError:
                    pass
            vid = video_bits_per_second(opts["encoder"], opts["crf"], w, h, fps)
            if vid is None:
                return None
            return int((vid + audio_bits_per_second(opts["audio"])) * dur / 8)

        if mode == "compress":
            if opts["by_size"]:
                return int(opts["target_mb"] * 1024 * 1024)
            if not dur:
                return None
            cap = opts["height"]
            if cap and info.height and info.height <= cap:
                cap = 0
            w, h = target_dimensions(info, height=cap)
            vid = video_bits_per_second(opts["encoder"], opts["crf"], w, h,
                                        info.fps)
            if vid is None:
                return None
            return int((vid + audio_bits_per_second(opts["audio"])) * dur / 8)

        if mode == "resize":
            if not dur:
                return None
            w, h = target_dimensions(
                info, height=opts["height"], width=opts["custom_width"]
            )
            vid = video_bits_per_second(opts["encoder"], opts["crf"], w, h,
                                        info.fps)
            if vid is None:
                return None
            return int((vid + 128_000) * dur / 8)

        if mode == "trim":
            start = opts["start"] or 0
            end = opts["end"]
            span = (end - start) if end is not None else (
                (dur - start) if dur else None)
            if not span or span <= 0:
                return None
            if not opts["precise"]:
                if not (info.size and dur):
                    return None
                return int(info.size * min(1.0, span / dur))
            vid = video_bits_per_second(
                "libx264", opts["crf"], info.width, info.height, info.fps
            )
            if vid is None:
                return None
            return int((vid + 192_000) * span / 8)

        if mode == "audio":
            if not dur:
                return None
            codec = opts["codec"]
            if codec == "copy":
                return None
            if codec == "pcm_s16le":
                rate = 44100 if opts["rate"] == "Keep original" else int(
                    opts["rate"])
                ch = 1 if opts["channels"] == "Mono" else 2
                return int(rate * ch * 2 * dur)
            if codec == "flac":
                rate = 44100 if opts["rate"] == "Keep original" else int(
                    opts["rate"])
                ch = 1 if opts["channels"] == "Mono" else 2
                return int(rate * ch * 2 * dur * 0.6)
            kbps = int(re.sub(r"\D", "", opts["bitrate"]) or 192)
            return int(kbps * 1000 * dur / 8)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return None


# ---------------------------------------------------------------- photos


PHOTO_FORMATS = [
    ("Keep original", ""),
    ("JPEG", ".jpg"),
    ("PNG", ".png"),
    ("WebP", ".webp"),
    ("TIFF", ".tiff"),
    ("BMP", ".bmp"),
]

RESAMPLERS = [
    ("Lanczos (sharpest)", "lanczos"),
    ("Bicubic", "bicubic"),
    ("Bilinear (softest)", "bilinear"),
    ("Nearest (pixel art)", "neighbor"),
]

CROP_ASPECTS = [
    ("Free (set the box)", None),
    ("Square 1:1", 1.0),
    ("Portrait 4:5", 4 / 5),
    ("Portrait 2:3", 2 / 3),
    ("Portrait 9:16", 9 / 16),
    ("Landscape 3:2", 3 / 2),
    ("Landscape 4:3", 4 / 3),
    ("Landscape 16:9", 16 / 9),
]

CROP_ANCHORS = ["Center", "Top", "Bottom", "Left", "Right"]


def photo_format_choices(ff: FFmpeg):
    out = list(PHOTO_FORMATS)
    if ff.has("libaom-av1"):
        out.append(("AVIF", ".avif"))
    return out


def lossy_ext(ext):
    return ext.lower() in (".jpg", ".jpeg", ".webp", ".avif")


def image_codec_args(ext, quality=90, lossless=False, strip=False):
    """Encoder flags for writing one still image in the given format."""
    args = []
    e = (ext or "").lower()
    if e in (".jpg", ".jpeg"):
        q = int(max(2, min(31, round(31 - (quality / 100.0) * 29))))
        args += ["-c:v", "mjpeg", "-q:v", str(q)]
    elif e == ".png":
        args += ["-c:v", "png", "-compression_level", "6"]
    elif e == ".webp":
        if lossless:
            args += ["-c:v", "libwebp", "-lossless", "1"]
        else:
            args += ["-c:v", "libwebp", "-quality", str(int(quality))]
    elif e in (".tif", ".tiff"):
        args += ["-c:v", "tiff", "-compression_algo", "deflate"]
    elif e == ".bmp":
        args += ["-c:v", "bmp"]
    elif e == ".avif":
        crf = int(max(0, min(63, round(63 - (quality / 100.0) * 55))))
        args += ["-c:v", "libaom-av1", "-crf", str(crf), "-still-picture", "1"]
    if strip:
        args += ["-map_metadata", "-1"]
    return args


def anchor_xy(anchor):
    """crop x/y expressions for a given anchor, using crop's ow/oh vars."""
    return {
        "Center": ("(iw-ow)/2", "(ih-oh)/2"),
        "Top": ("(iw-ow)/2", "0"),
        "Bottom": ("(iw-ow)/2", "ih-oh"),
        "Left": ("0", "(ih-oh)/2"),
        "Right": ("iw-ow", "(ih-oh)/2"),
    }.get(anchor, ("(iw-ow)/2", "(ih-oh)/2"))


class PhotoPanel(ModePanel):
    """Shared format and quality controls for every photo mode."""

    def add_output_fields(self, default_index=0):
        self.fmt_map = photo_format_choices(self.ff)
        self.fmt = Combo([f[0] for f in self.fmt_map])
        self.fmt.setCurrentIndex(default_index)
        self.quality = SliderField(
            "Quality", 30, 100, 90,
            lambda v: "%d  (%s)" % (
                v, "maximum" if v >= 95 else "high" if v >= 85
                else "balanced" if v >= 70 else "small file"),
        )
        self.strip = Toggle(False)
        self.strip_row = QWidget()
        rl = QHBoxLayout(self.strip_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(self.strip)
        rl.addWidget(label("Strip EXIF and location data", TEXT, 11))
        rl.addStretch(1)

    def wire_output_fields(self):
        self.fmt.currentIndexChanged.connect(self._sync_quality)
        self.wire(self.fmt, self.quality, self.strip)
        self._sync_quality()

    def _sync_quality(self):
        ext = self.fmt_map[self.fmt.currentIndex()][1]
        self.quality.setEnabled(lossy_ext(ext) or ext == "")

    def out_ext(self, info):
        ext = self.fmt_map[self.fmt.currentIndex()][1]
        return ext or (info.path.suffix or ".png")

    def extension(self, info):
        return self.out_ext(info)

    def output_values(self):
        return {
            "ext": self.fmt_map[self.fmt.currentIndex()][1],
            "quality": self.quality.value(),
            "strip": self.strip.isChecked(),
        }


class PhotoConvertPanel(PhotoPanel):
    def build(self):
        self.add_output_fields(default_index=1)
        self.lossless = Toggle(False)
        self.cap = QSpinBox()
        self.cap.setRange(0, 16384)
        self.cap.setValue(0)
        self.cap.setSpecialValueText("No limit")
        self.cap.setSuffix("  px long edge")
        self.cap.setMinimumHeight(34)

        lossrow = QWidget()
        ll = QHBoxLayout(lossrow)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        ll.addWidget(self.lossless)
        ll.addWidget(label("Lossless (WebP only)", TEXT, 11))
        ll.addStretch(1)

        self.add(Field("Format", self.fmt), 0)
        self.add(Field("Size cap", self.cap, "shrinks only, never enlarges"), 1)
        self.add(Field("WebP mode", lossrow), 2)
        self.add(Field("Metadata", self.strip_row), 0, 3)
        self.add(self.quality, 0, 3)

        self.wire_output_fields()
        self.wire(self.lossless, self.cap)

    def suffix(self):
        return "_converted"

    def values(self):
        v = self.output_values()
        v.update(lossless=self.lossless.isChecked(), cap=self.cap.value())
        return v


class PhotoResizePanel(PhotoPanel):
    BASES = ["Long edge", "Width", "Height", "Percentage", "Exact size"]

    def build(self):
        self.basis = Combo(self.BASES)
        self.value = QSpinBox()
        self.value.setRange(1, 16384)
        self.value.setValue(1920)
        self.value.setSuffix("  px")
        self.value.setMinimumHeight(34)
        self.pct = QSpinBox()
        self.pct.setRange(1, 400)
        self.pct.setValue(50)
        self.pct.setSuffix("  %")
        self.pct.setMinimumHeight(34)
        self.box_w = QSpinBox()
        self.box_w.setRange(1, 16384)
        self.box_w.setValue(1080)
        self.box_w.setSuffix("  w")
        self.box_w.setMinimumHeight(34)
        self.box_h = QSpinBox()
        self.box_h.setRange(1, 16384)
        self.box_h.setValue(1080)
        self.box_h.setSuffix("  h")
        self.box_h.setMinimumHeight(34)
        self.fit = Combo([
            "Pad to fit (adds borders)", "Crop to fill", "Stretch to fit",
        ])
        self.resample = Combo([r[0] for r in RESAMPLERS])
        self.no_upscale = Toggle(True)
        nurow = QWidget()
        nl = QHBoxLayout(nurow)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(10)
        nl.addWidget(self.no_upscale)
        nl.addWidget(label("Never enlarge a smaller image", TEXT, 11))
        nl.addStretch(1)

        self.add_output_fields(default_index=0)

        self.target_stack = QStackedWidget()
        self.target_stack.addWidget(self.value)
        self.target_stack.addWidget(self.pct)
        self.target_stack.addWidget(self._box_row())
        self.target_stack.setFixedHeight(38)

        self.add(Field("Resize by", self.basis), 0)
        self.target_field = Field("Target", self.target_stack)
        self.add(self.target_field, 1)
        self.add(Field("Resampling", self.resample), 2)
        self.add(Field("Fit", self.fit), 0)
        self.add(Field("Format", self.fmt), 1)
        self.add(Field("Upscaling", nurow), 2)
        self.add(self.quality, 0, 3)

        self.basis.currentIndexChanged.connect(self._sync)
        self.wire_output_fields()
        self.wire(self.basis, self.value, self.pct, self.box_w, self.box_h,
                  self.fit, self.resample, self.no_upscale)
        self._sync()

    def _box_row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.box_w)
        lay.addWidget(self.box_h)
        return w

    def _sync(self):
        i = self.basis.currentIndex()
        self.target_stack.setCurrentIndex(0 if i in (0, 1, 2) else
                                          1 if i == 3 else 2)
        self.value.setSuffix(
            {0: "  px long edge", 1: "  px wide", 2: "  px tall"}.get(i, "  px")
        )
        self.fit.setEnabled(i == 4)

    def suffix(self):
        return "_resized"

    def values(self):
        v = self.output_values()
        v.update(
            basis=self.basis.currentIndex(),
            value=self.value.value(),
            pct=self.pct.value(),
            box_w=self.box_w.value(),
            box_h=self.box_h.value(),
            fit=self.fit.currentIndex(),
            flags=RESAMPLERS[self.resample.currentIndex()][1],
            no_upscale=self.no_upscale.isChecked(),
        )
        return v


class PhotoCropPanel(PhotoPanel):
    def build(self):
        self.aspect = Combo([a[0] for a in CROP_ASPECTS])
        self.anchor = Combo(CROP_ANCHORS)
        self.cx = QSpinBox(); self.cx.setRange(0, 16384); self.cx.setSuffix("  x")
        self.cy = QSpinBox(); self.cy.setRange(0, 16384); self.cy.setSuffix("  y")
        self.cw = QSpinBox(); self.cw.setRange(1, 16384); self.cw.setValue(1000); self.cw.setSuffix("  w")
        self.ch = QSpinBox(); self.ch.setRange(1, 16384); self.ch.setValue(1000); self.ch.setSuffix("  h")
        for sp in (self.cx, self.cy, self.cw, self.ch):
            sp.setMinimumHeight(34)

        self.add_output_fields(default_index=0)

        self.add(Field("Aspect", self.aspect), 0)
        self.add(Field("Keep which part", self.anchor), 1)
        self.add(Field("Format", self.fmt), 2)
        self.box_field = Field(
            "Crop box", self._box_row(), "pixels from the top left"
        )
        self.add(self.box_field, 0, 3)
        self.add(self.quality, 0, 3)

        self.aspect.currentIndexChanged.connect(self._sync)
        self.wire_output_fields()
        self.wire(self.aspect, self.anchor, self.cx, self.cy, self.cw, self.ch)
        self._sync()

    def _box_row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for sp in (self.cx, self.cy, self.cw, self.ch):
            lay.addWidget(sp)
        lay.addStretch(1)
        return w

    def _sync(self):
        free = CROP_ASPECTS[self.aspect.currentIndex()][1] is None
        self.box_field.setVisible(free)
        self.anchor.setEnabled(not free)

    def suffix(self):
        return "_cropped"

    def values(self):
        v = self.output_values()
        v.update(
            ratio=CROP_ASPECTS[self.aspect.currentIndex()][1],
            anchor=self.anchor.currentText(),
            box=(self.cx.value(), self.cy.value(),
                 self.cw.value(), self.ch.value()),
        )
        return v


class PhotoAdjustPanel(PhotoPanel):
    def build(self):
        self.brightness = SliderField(
            "Brightness", -100, 100, 0, lambda v: ("+" if v > 0 else "") + str(v))
        self.contrast = SliderField(
            "Contrast", -100, 100, 0, lambda v: ("+" if v > 0 else "") + str(v))
        self.saturation = SliderField(
            "Saturation", -100, 100, 0, lambda v: ("+" if v > 0 else "") + str(v))
        self.gamma = SliderField(
            "Gamma", 50, 200, 100, lambda v: "%.2f" % (v / 100.0))
        self.sharpen = Combo(["None", "Light", "Medium", "Strong"])
        self.grayscale = Toggle(False)
        self.transform = Combo([
            "None", "Rotate 90° right", "Rotate 90° left",
            "Rotate 180°", "Flip horizontal", "Flip vertical",
        ])
        grow = QWidget()
        gl = QHBoxLayout(grow)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(10)
        gl.addWidget(self.grayscale)
        gl.addWidget(label("Black and white", TEXT, 11))
        gl.addStretch(1)

        self.add_output_fields(default_index=0)

        self.add(Field("Sharpen", self.sharpen), 0)
        self.add(Field("Transform", self.transform), 1)
        self.add(Field("Format", self.fmt), 2)
        self.add(Field("Color", grow), 0, 3)
        self.add(self.brightness, 0, 3)
        self.add(self.contrast, 0, 3)
        self.add(self.saturation, 0, 3)
        self.add(self.gamma, 0, 3)
        self.add(self.quality, 0, 3)

        self.wire_output_fields()
        self.wire(self.brightness, self.contrast, self.saturation, self.gamma,
                  self.sharpen, self.grayscale, self.transform)

    def suffix(self):
        return "_adjusted"

    def values(self):
        v = self.output_values()
        v.update(
            brightness=self.brightness.value() / 100.0,
            contrast=1.0 + self.contrast.value() / 100.0,
            saturation=1.0 + self.saturation.value() / 100.0,
            gamma=self.gamma.value() / 100.0,
            sharpen=self.sharpen.currentIndex(),
            grayscale=self.grayscale.isChecked(),
            transform=self.transform.currentText(),
        )
        return v


def photo_filters(mode, opts, info):
    """Filter chain for a photo job, in the order the filters must run."""
    filters = []

    if mode == "photo_convert":
        cap = opts.get("cap") or 0
        if cap:
            filters.append(
                "scale=w='if(gt(iw,ih),min(iw,%d),-1)':"
                "h='if(gt(iw,ih),-1,min(ih,%d))':flags=lanczos" % (cap, cap)
            )

    elif mode == "photo_resize":
        flags = opts["flags"]
        basis = opts["basis"]
        no_up = opts["no_upscale"]
        if basis == 0:
            n = opts["value"]
            w_expr = "min(iw,%d)" % n if no_up else str(n)
            h_expr = "min(ih,%d)" % n if no_up else str(n)
            filters.append(
                "scale=w='if(gt(iw,ih),%s,-1)':h='if(gt(iw,ih),-1,%s)':flags=%s"
                % (w_expr, h_expr, flags)
            )
        elif basis == 1:
            n = opts["value"]
            expr = "min(iw,%d)" % n if no_up else str(n)
            filters.append("scale=w='%s':h=-1:flags=%s" % (expr, flags))
        elif basis == 2:
            n = opts["value"]
            expr = "min(ih,%d)" % n if no_up else str(n)
            filters.append("scale=w=-1:h='%s':flags=%s" % (expr, flags))
        elif basis == 3:
            p = opts["pct"] / 100.0
            filters.append(
                "scale=w='iw*%0.4f':h='ih*%0.4f':flags=%s" % (p, p, flags)
            )
        else:
            w, h = opts["box_w"], opts["box_h"]
            if opts["fit"] == 0:
                filters.append(
                    "scale=%d:%d:force_original_aspect_ratio=decrease:flags=%s"
                    % (w, h, flags)
                )
                filters.append(
                    "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black" % (w, h)
                )
            elif opts["fit"] == 1:
                filters.append(
                    "scale=%d:%d:force_original_aspect_ratio=increase:flags=%s"
                    % (w, h, flags)
                )
                filters.append("crop=%d:%d" % (w, h))
            else:
                filters.append("scale=%d:%d:flags=%s" % (w, h, flags))

    elif mode == "photo_crop":
        ratio = opts["ratio"]
        if ratio is None:
            x, y, w, h = opts["box"]
            filters.append("crop=w=%d:h=%d:x=%d:y=%d" % (w, h, x, y))
        else:
            ax, ay = anchor_xy(opts["anchor"])
            filters.append(
                "crop=w='min(iw,ih*%0.6f)':h='min(ih,iw/%0.6f)':x=%s:y=%s"
                % (ratio, ratio, ax, ay)
            )

    elif mode == "photo_adjust":
        eq = []
        if abs(opts["brightness"]) > 0.001:
            eq.append("brightness=%0.3f" % opts["brightness"])
        if abs(opts["contrast"] - 1.0) > 0.001:
            eq.append("contrast=%0.3f" % opts["contrast"])
        if abs(opts["saturation"] - 1.0) > 0.001:
            eq.append("saturation=%0.3f" % opts["saturation"])
        if abs(opts["gamma"] - 1.0) > 0.001:
            eq.append("gamma=%0.3f" % opts["gamma"])
        if eq:
            filters.append("eq=" + ":".join(eq))
        if opts["grayscale"]:
            filters.append("hue=s=0")
        sharp = {0: "", 1: "unsharp=5:5:0.6", 2: "unsharp=5:5:1.1",
                 3: "unsharp=5:5:1.8"}[opts["sharpen"]]
        if sharp:
            filters.append(sharp)
        tf = TRANSFORM_MAP.get(opts["transform"], "")
        if tf:
            filters.extend(tf.split(","))

    return [f for f in filters if f]


# ---------------------------------------------------------------- updates


def parse_version(text):
    """Pull a comparable numeric version out of any ffmpeg version string."""
    if not text:
        return None
    m = re.search(r"ffmpeg version\s+(\S+)", text)
    raw = m.group(1) if m else text.strip()
    if re.match(r"^[nN]\d", raw):
        raw = raw[1:]
    m2 = re.match(r"(\d+(?:\.\d+)*)", raw)
    if not m2:
        return None
    return tuple(int(x) for x in m2.group(1).split("."))


def version_text(ver):
    return ".".join(str(x) for x in ver) if ver else "unknown"


def newer(latest, current):
    """True when latest is strictly newer, comparing on equal footing."""
    if not latest or not current:
        return False
    size = max(len(latest), len(current))
    a = tuple(latest) + (0,) * (size - len(latest))
    b = tuple(current) + (0,) * (size - len(current))
    return a > b


def fetch_bytes(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_text(url, timeout=25):
    return fetch_bytes(url, timeout).decode("utf-8", "replace").strip()


class UpdateCheck(QThread):
    """Asks the build server what the newest ffmpeg release is."""

    done = Signal(dict)

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.current = current

    def run(self):
        out = {
            "ok": False, "latest": None, "current": self.current,
            "available": False, "error": "",
        }
        latest = None
        errors = []
        try:
            latest = parse_version(fetch_text(UPDATE_VERSION_URL))
        except Exception as exc:
            errors.append(str(exc))
        if latest is None:
            try:
                html = fetch_text(UPDATE_FALLBACK_PAGE, timeout=25)
                found = re.findall(
                    r"(\d+\.\d+(?:\.\d+)?)\s+was released on", html
                )
                cands = [parse_version(f) for f in found]
                cands = [c for c in cands if c]
                if cands:
                    latest = max(cands)
            except Exception as exc:
                errors.append(str(exc))

        if latest is None:
            out["error"] = errors[0] if errors else "no version returned"
            self.done.emit(out)
            return

        out["ok"] = True
        out["latest"] = latest
        out["available"] = newer(latest, self.current)
        self.done.emit(out)


class UpdateInstall(QThread):
    """Downloads a verified ffmpeg build and swaps it into the app folder."""

    progress = Signal(int, str)
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        if not IS_WIN:
            self.failed.emit(
                "Automatic install only covers Windows. On this platform, "
                "update ffmpeg with your package manager."
            )
            return

        tmp = Path(tempfile.mkdtemp(prefix="ffforge_update_"))
        try:
            zip_path = tmp / "ffmpeg.zip"
            self.progress.emit(0, "Contacting the build server")

            want_sha = ""
            try:
                want_sha = fetch_text(UPDATE_SHA_URL).split()[0].lower()
            except Exception:
                want_sha = ""

            req = urllib.request.Request(UPDATE_ZIP_URL, headers=UA)
            digest = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                with open(zip_path, "wb") as fh:
                    while True:
                        if self._stop:
                            self.failed.emit("Cancelled.")
                            return
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        digest.update(chunk)
                        got += len(chunk)
                        pct = int(got * 80 / total) if total else 0
                        self.progress.emit(
                            min(80, pct),
                            "Downloading %s of %s"
                            % (human_size(got), human_size(total) if total else "?"),
                        )

            if want_sha:
                self.progress.emit(82, "Verifying checksum")
                if digest.hexdigest().lower() != want_sha:
                    self.failed.emit(
                        "The download did not match the published checksum. "
                        "Nothing was changed."
                    )
                    return

            self.progress.emit(85, "Extracting")
            extract_to = tmp / "x"
            with zipfile.ZipFile(zip_path) as zf:
                member = next(
                    (n for n in zf.namelist()
                     if n.lower().endswith("bin/ffmpeg.exe")), None
                )
                if not member:
                    self.failed.emit("That archive has no ffmpeg.exe in it.")
                    return
                zf.extractall(extract_to)
            new_root = (extract_to / member).parent.parent

            self.progress.emit(92, "Installing")
            target = managed_dir()
            backup = target.with_name("ffmpeg.previous")
            shutil.rmtree(backup, ignore_errors=True)
            restored = False
            try:
                if target.exists():
                    target.rename(backup)
                    restored = True
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(new_root), str(target))
            except OSError as exc:
                if restored and backup.exists() and not target.exists():
                    backup.rename(target)
                self.failed.emit(
                    "Could not replace the existing copy: %s" % exc
                )
                return

            self.progress.emit(97, "Checking the new build")
            probe = FFmpeg()
            if not probe.locate(str(managed_exe())):
                shutil.rmtree(target, ignore_errors=True)
                if backup.exists():
                    backup.rename(target)
                self.failed.emit(
                    "The new build would not run, so the old one was put back."
                )
                return

            shutil.rmtree(backup, ignore_errors=True)
            self.progress.emit(100, "Done")
            self.ok.emit(str(managed_exe()))
        except urllib.error.URLError as exc:
            self.failed.emit("Could not reach the build server: %s" % exc.reason)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BadgeButton(QPushButton):
    """A button that can wear a small alert dot in its top-right corner."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._badge = False

    def set_badge(self, on):
        if self._badge != bool(on):
            self._badge = bool(on)
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._badge:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() - 8.5, 8.5)
        p.setPen(QPen(QColor(BG), 2))
        p.setBrush(QColor(BAD))
        p.drawEllipse(centre, 4.5, 4.5)
        p.end()


# ---------------------------------------------------------------- settings


class SettingsDialog(QDialog):
    update_applied = Signal()

    def __init__(self, ff: FFmpeg, settings: QSettings, parent=None,
                 last_check=None, busy=False):
        super().__init__(parent)
        self.ff = ff
        self.settings = settings
        self.busy = busy
        self.checker = None
        self.installer = None
        self.latest = last_check.get("latest") if last_check else None
        self.have_result = bool(last_check and last_check.get("ok"))
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(18)

        lay.addWidget(label("FFMPEG", MUTED, 10, True))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(ff.ffmpeg)
        self.path_edit.setMinimumHeight(34)
        self.path_edit.setPlaceholderText("path to ffmpeg executable")
        browse = QPushButton("Browse")
        browse.setObjectName("secondary")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        self.status = label(
            ff.version or "not found", GOOD if ff.ready else BAD, 11
        )
        lay.addWidget(self.status)

        gpu = [e for e in ("h264_nvenc", "hevc_nvenc", "av1_nvenc")
               if ff.has(e)]
        lay.addWidget(label(
            "GPU encoders: " + (", ".join(gpu) if gpu else "none detected"),
            MUTED, 11,
        ))

        lay.addWidget(label("UPDATES", MUTED, 10, True))
        self.upd_status = label("Checking for updates...", MUTED, 12)
        self.upd_status.setWordWrap(True)
        lay.addWidget(self.upd_status)
        self.upd_detail = label("", DIM, 11)
        self.upd_detail.setWordWrap(True)
        lay.addWidget(self.upd_detail)

        self.upd_bar = QProgressBar()
        self.upd_bar.setRange(0, 100)
        self.upd_bar.setTextVisible(False)
        self.upd_bar.setFixedHeight(6)
        self.upd_bar.hide()
        lay.addWidget(self.upd_bar)

        urow = QHBoxLayout()
        self.check_btn = QPushButton("Check now")
        self.check_btn.setObjectName("secondary")
        self.check_btn.clicked.connect(lambda: self.start_check(force=True))
        self.install_btn = QPushButton("Update ffmpeg")
        self.install_btn.setObjectName("primary")
        self.install_btn.clicked.connect(self._install)
        self.install_btn.hide()
        urow.addWidget(self.check_btn)
        urow.addWidget(self.install_btn)
        urow.addStretch(1)
        lay.addLayout(urow)

        lay.addWidget(label("DEFAULT OUTPUT FOLDER", MUTED, 10, True))
        row2 = QHBoxLayout()
        self.out_edit = QLineEdit(settings.value("out_dir", "", str))
        self.out_edit.setMinimumHeight(34)
        self.out_edit.setPlaceholderText("same folder as the source file")
        pick = QPushButton("Browse")
        pick.setObjectName("secondary")
        pick.clicked.connect(self._pick_dir)
        row2.addWidget(self.out_edit, 1)
        row2.addWidget(pick)
        lay.addLayout(row2)

        self.open_after = QCheckBox("Open the folder when a batch finishes")
        self.open_after.setChecked(settings.value("open_after", True, bool))
        lay.addWidget(self.open_after)

        lay.addStretch(1)
        self._render_update_state()
        if not self.have_result:
            self.start_check(force=False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    # ------------------------------------------------------ update logic

    def start_check(self, force=False):
        if self.checker and self.checker.isRunning():
            return
        self.upd_status.setText("Checking for updates...")
        self.upd_status.setStyleSheet(
            f"color:{MUTED}; font-size:12px; background:transparent;"
        )
        self.upd_detail.setText("")
        self.install_btn.hide()
        self.check_btn.setEnabled(False)
        self.checker = UpdateCheck(self.ff.numeric_version, self)
        self.checker.done.connect(self._on_check)
        self.checker.start()

    def _on_check(self, result):
        self.check_btn.setEnabled(True)
        self.have_result = result.get("ok", False)
        self.latest = result.get("latest")
        self.check_error = result.get("error", "")
        self._render_update_state()
        parent = self.parent()
        if parent is not None and hasattr(parent, "apply_update_state"):
            parent.apply_update_state(result)

    def _render_update_state(self):
        cur = self.ff.numeric_version
        self.install_btn.hide()

        if not self.have_result:
            if self.checker and self.checker.isRunning():
                return
            self.upd_status.setText("Could not reach the update server.")
            self.upd_status.setStyleSheet(
                f"color:{WARN}; font-size:12px; background:transparent;"
            )
            self.upd_detail.setText(
                getattr(self, "check_error", "") or
                "Check your internet connection and try again."
            )
            return

        latest_s = version_text(self.latest)

        if not IS_WIN:
            if newer(self.latest, cur):
                self.upd_status.setText(
                    "Update available: ffmpeg %s (you have %s)"
                    % (latest_s, version_text(cur))
                )
                self.upd_status.setStyleSheet(
                    f"color:{ACCENT_HI}; font-size:12px; background:transparent;"
                )
            else:
                self.upd_status.setText(
                    "Up to date. ffmpeg %s is the latest release."
                    % version_text(cur)
                )
                self.upd_status.setStyleSheet(
                    f"color:{GOOD}; font-size:12px; background:transparent;"
                )
            self.upd_detail.setText(
                "Install updates through your package manager on this platform."
            )
            return

        if not self.ff.ready:
            self.upd_status.setText("ffmpeg is not set up yet.")
            self.upd_status.setStyleSheet(
                f"color:{BAD}; font-size:12px; background:transparent;"
            )
            self.upd_detail.setText(
                "FFMPEG-Forge can download ffmpeg %s into its own folder."
                % latest_s
            )
            self.install_btn.setText("Install ffmpeg %s" % latest_s)
            self.install_btn.show()
        elif cur is None:
            self.upd_status.setText(
                "You are running a custom or development build."
            )
            self.upd_status.setStyleSheet(
                f"color:{MUTED}; font-size:12px; background:transparent;"
            )
            self.upd_detail.setText(
                "Nothing to compare against a release number. The latest "
                "release build is %s if you want to switch to it." % latest_s
            )
            self.install_btn.setText("Install release %s" % latest_s)
            self.install_btn.show()
        elif newer(self.latest, cur):
            self.upd_status.setText(
                "Update available: ffmpeg %s (you have %s)"
                % (latest_s, version_text(cur))
            )
            self.upd_status.setStyleSheet(
                f"color:{ACCENT_HI}; font-size:12px; background:transparent;"
            )
            if self.ff.is_managed:
                self.upd_detail.setText(
                    "This replaces the copy in the FFMPEG-Forge folder. The "
                    "old one is kept until the new build is confirmed working."
                )
                self.install_btn.setText("Update to %s" % latest_s)
            else:
                self.upd_detail.setText(
                    "Your ffmpeg lives at %s. FFMPEG-Forge will install its "
                    "own copy alongside the app and use that instead, leaving "
                    "the system one untouched." % self.ff.ffmpeg
                )
                self.install_btn.setText("Install %s here" % latest_s)
            self.install_btn.show()
        else:
            self.upd_status.setText(
                "Up to date. ffmpeg %s is the latest release."
                % version_text(cur)
            )
            self.upd_status.setStyleSheet(
                f"color:{GOOD}; font-size:12px; background:transparent;"
            )
            self.upd_detail.setText(
                "Checked against the official release builds."
            )

        if self.busy and not self.install_btn.isHidden():
            self.install_btn.setEnabled(False)
            self.upd_detail.setText(
                "Finish or stop the current render before updating ffmpeg."
            )

    def _install(self):
        if self.installer and self.installer.isRunning():
            return
        self.install_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.upd_bar.setValue(0)
        self.upd_bar.show()
        self.installer = UpdateInstall(self)
        self.installer.progress.connect(self._on_install_progress)
        self.installer.ok.connect(self._on_install_ok)
        self.installer.failed.connect(self._on_install_failed)
        self.installer.start()

    def _on_install_progress(self, pct, stage):
        self.upd_bar.setValue(pct)
        self.upd_detail.setText(stage)

    def _on_install_ok(self, path):
        self.upd_bar.hide()
        self.check_btn.setEnabled(True)
        self.settings.setValue("ffmpeg", path)
        self.ff.locate(path)
        self.path_edit.setText(path)
        self.status.setText(self.ff.version or "not found")
        self.status.setStyleSheet(
            f"color:{GOOD}; font-size:11px; background:transparent;"
        )
        self.have_result = True
        self.latest = self.ff.numeric_version
        self._render_update_state()
        self.update_applied.emit()

    def _on_install_failed(self, message):
        self.upd_bar.hide()
        self.check_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        self.upd_status.setText("Update failed.")
        self.upd_status.setStyleSheet(
            f"color:{BAD}; font-size:12px; background:transparent;"
        )
        self.upd_detail.setText(message)

    def stop_threads(self):
        """Never let this dialog die with a worker still attached to it."""
        for t in (self.checker, self.installer):
            if t and t.isRunning():
                if t is self.installer:
                    t.cancel()
                t.wait(5000)

    def reject(self):
        self.stop_threads()
        super().reject()

    def accept(self):
        self.stop_threads()
        super().accept()

    def closeEvent(self, event):
        self.stop_threads()
        super().closeEvent(event)

    def _browse(self):
        filt = "ffmpeg (ffmpeg.exe)" if IS_WIN else "ffmpeg (ffmpeg)"
        path, _ = QFileDialog.getOpenFileName(self, "Locate ffmpeg", "", filt)
        if path:
            self.path_edit.setText(path)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path:
            self.out_edit.setText(path)

    def _save(self):
        manual = self.path_edit.text().strip()
        if manual and not self.ff.locate(manual):
            QMessageBox.warning(
                self, "Not ffmpeg",
                "That file did not answer to -version. Check the path.",
            )
            return
        self.settings.setValue("ffmpeg", manual)
        self.settings.setValue("out_dir", self.out_edit.text().strip())
        self.settings.setValue("open_after", self.open_after.isChecked())
        self.accept()


# ---------------------------------------------------------------- main


class LiveImagePreview(QWidget):
    """Fast, local preview of photo edits with a draggable crop box."""

    boxChanged = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.source = QImage()
        self.result = QImage()
        self.crop = QRectF()
        self.dragging = False
        self.drag_origin = QPointF()

    def set_image(self, image, result=None, crop=None):
        self.source = image if image is not None else QImage()
        self.result = result if result is not None else self.source
        self.crop = QRectF(*crop) if crop else QRectF()
        self.update()

    def _layout(self):
        margin, gap = 14, 18
        w = max(1, (self.width() - margin * 2 - gap) // 2)
        h = self.height() - margin * 2 - 24
        return QRectF(margin, margin + 20, w, max(1, h)), QRectF(margin + w + gap, margin + 20, w, max(1, h))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PANEL))
        p.setPen(QColor(MUTED)); p.drawText(14, 17, "SOURCE")
        left, right = self._layout()
        p.setPen(QColor(BORDER)); p.drawRect(left); p.drawRect(right)
        for image, rect in ((self.source, left), (self.result, right)):
            if not image or image.isNull():
                continue
            pix = QPixmap.fromImage(image).scaled(rect.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            target = QRectF(rect.x() + (rect.width()-pix.width())/2, rect.y() + (rect.height()-pix.height())/2, pix.width(), pix.height())
            p.drawPixmap(target.topLeft(), pix)
        p.setPen(QColor(MUTED)); p.drawText(int(right.x()), 17, "LIVE RESULT")
        if self.crop and not self.source.isNull():
            sx = left.width() / self.source.width(); sy = left.height() / self.source.height()
            fit = min(sx, sy); pixw, pixh = self.source.width()*fit, self.source.height()*fit
            ox, oy = left.x() + (left.width()-pixw)/2, left.y() + (left.height()-pixh)/2
            r = QRectF(ox+self.crop.x()*fit, oy+self.crop.y()*fit, self.crop.width()*fit, self.crop.height()*fit)
            p.setPen(QPen(QColor(ACCENT_HI), 2)); p.setBrush(Qt.NoBrush); p.drawRect(r)

    def mousePressEvent(self, event):
        if self.source.isNull() or event.button() != Qt.LeftButton:
            return
        left, _ = self._layout(); fit = min(left.width()/self.source.width(), left.height()/self.source.height())
        ox = left.x() + (left.width()-self.source.width()*fit)/2; oy = left.y() + (left.height()-self.source.height()*fit)/2
        self.dragging = True; self.drag_origin = QPointF((event.position().x()-ox)/fit, (event.position().y()-oy)/fit)

    def mouseMoveEvent(self, event):
        if not self.dragging or self.source.isNull():
            return
        left, _ = self._layout(); fit = min(left.width()/self.source.width(), left.height()/self.source.height())
        ox = left.x() + (left.width()-self.source.width()*fit)/2; oy = left.y() + (left.height()-self.source.height()*fit)/2
        x = max(0, min(self.source.width()-1, int((event.position().x()-ox)/fit))); y = max(0, min(self.source.height()-1, int((event.position().y()-oy)/fit)))
        x0, y0 = int(self.drag_origin.x()), int(self.drag_origin.y()); rx, ry = min(x0,x), min(y0,y); rw, rh = abs(x-x0), abs(y-y0)
        if rw > 4 and rh > 4:
            self.crop = QRectF(rx, ry, rw, rh); self.boxChanged.emit(rx, ry, rw, rh); self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = False


class ForgeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.ff = FFmpeg()
        self.ff.locate(self.settings.value("ffmpeg", "", str))
        self.rows: list[QueueRow] = []
        self.worker = None
        self.update_state = {}
        self.startup_check = None
        self.section = self.settings.value("section", "video", str)
        self.render_rows = []
        self.render_started = 0.0

        self.setWindowTitle("FFMPEG-Forge")
        self.setObjectName("forgeRoot")
        self.setMinimumSize(1000, 700)
        self.resize(1180, 880)
        self.setAcceptDrops(True)
        self.setStyleSheet(self._qss())
        self._build()
        self._mode_fade = QPropertyAnimation(self.panels, b"windowOpacity", self)
        self._mode_fade.setDuration(180)
        self._refresh_ffmpeg_pill()
        self._update_preview()
        self._start_background_check()

    # ---------------------------------------------------------- styling

    def _qss(self):
        return f"""
        QWidget#forgeRoot {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #0b1230, stop:0.45 #080b1f, stop:1 #160d32);
            border: 1px solid #293b70;
            border-radius: 16px;
        }}
        QWidget {{
            background: #080b1f;
            color: {TEXT};
            font-family: "{UIFONT}", "Segoe UI", sans-serif;
            font-size: 12px;
        }}
        QFrame#card {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #182650, stop:0.42 #111936, stop:1 #130f2c);
            border: 1px solid {BORDER};
            border-radius: 14px;
        }}
        QFrame#sidebar {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #14234a, stop:0.6 #101733, stop:1 #1a1038);
            border: 1px solid {BORDER};
            border-radius: 14px;
        }}
        QLineEdit, QSpinBox {{
            background: {FIELD};
            border: 1px solid {BORDER};
            border-radius: 8px;
            padding: 6px 10px;
            color: {TEXT};
            selection-background-color: {ACCENT};
        }}
        QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 16px; border: none; background: transparent;
        }}
        QComboBox QAbstractItemView {{
            background: {PANEL_2};
            border: 1px solid {BORDER_HI};
            border-radius: 8px;
            padding: 4px;
            outline: none;
            selection-background-color: {ACCENT};
            selection-color: #ffffff;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 7px 10px; border-radius: 6px; min-height: 20px;
        }}
        QPushButton {{
            background: {FIELD};
            border: 1px solid {BORDER};
            border-radius: 8px;
            padding: 8px 16px;
            color: {TEXT};
        }}
        QPushButton:hover {{ border: 1px solid {BORDER_HI}; background: {PANEL_2}; }}
        QPushButton#primary {{
            background: {ACCENT}; border: none; color: #0b0d14;
            font-weight: 700; padding: 11px 30px; border-radius: 10px;
        }}
        QPushButton#primary:hover {{ background: {ACCENT_HI}; }}
        QPushButton#primary:disabled {{ background: {BORDER}; color: {DIM}; }}
        QPushButton#secondary {{ background: {FIELD}; }}
        QPushButton#ghost {{
            background: transparent; border: none; color: {DIM};
            padding: 0px; font-size: 13px;
        }}
        QPushButton#ghost:hover {{ color: {BAD}; background: transparent; }}
        QWidget#segment {{
            background: {FIELD};
            border: 1px solid {BORDER};
            border-radius: 10px;
        }}
        QPushButton#seg {{
            background: transparent; border: none; border-radius: 7px;
            padding: 7px 0px; color: {MUTED}; font-size: 12px;
        }}
        QPushButton#seg:hover {{ color: {TEXT}; background: {PANEL_2}; }}
        QPushButton#seg:checked {{
            background: {ACCENT}; color: #0b0d14; font-weight: 700;
        }}
        QPushButton#mode {{
            background: transparent; border: none; text-align: left;
            padding: 10px 14px; border-radius: 9px; color: {MUTED};
            font-size: 12px;
        }}
        QPushButton#mode:hover {{ background: {PANEL_2}; color: {TEXT}; }}
        QPushButton#mode:checked {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1f4e82, stop:1 #4b2c78);
            color: #ffffff; font-weight: 700;
            border: 1px solid #5d8be0;
        }}
        QLabel {{ background: transparent; }}
        QPushButton {{
            background: #172650;
            color: {TEXT};
            border: 1px solid #38549a;
            border-radius: 10px;
            padding: 8px 14px;
        }}
        QPushButton:hover {{
            background: #254879;
            border-color: {ACCENT};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background: #633f9b;
            padding-top: 9px;
        }}
        QPushButton:disabled {{
            background: rgba(50, 65, 98, 105);
            color: rgba(232, 234, 242, 110);
            border-color: rgba(145, 169, 210, 70);
        }}
        QComboBox, QLineEdit, QSpinBox, QTextEdit {{
            background: #0b122c;
            color: {TEXT};
            border: 1px solid rgba(151, 181, 239, 120);
            border-radius: 9px;
            padding: 7px 10px;
            selection-background-color: rgba(120, 168, 255, 190);
        }}
        QComboBox:hover, QLineEdit:focus, QSpinBox:focus {{
            border-color: {ACCENT};
            background: #101e42;
        }}
        QComboBox QAbstractItemView {{
            background: rgba(18, 29, 57, 245);
            color: {TEXT};
            border: 1px solid {BORDER_HI};
            selection-background-color: rgba(104, 151, 242, 210);
        }}
        QFrame#row {{
            background: rgba(40, 58, 99, 155);
            border: 1px solid rgba(153, 182, 239, 95);
            border-radius: 10px;
        }}
        QFrame#row:hover {{ border: 1px solid {BORDER_HI}; }}
        QProgressBar {{
            background: rgba(8, 14, 30, 190); border: 1px solid rgba(165, 193, 245, 95); border-radius: 5px; height: 8px;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {ACCENT}, stop:0.5 {ACCENT_HI}, stop:1 #6d8dff);
            border-radius: 4px;
        }}
        QProgressBar#rowbar {{ background: {BORDER}; border-radius: 2px; }}
        QProgressBar#rowbar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
        QSlider::groove:horizontal {{
            height: 6px; background: rgba(8, 12, 25, 0.9); border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        QSlider::handle:horizontal {{
            background: {ACCENT_HI}; width: 16px; height: 16px;
            margin: -5px 0px; border-radius: 8px;
            border: 2px solid rgba(255,255,255,0.72);
        }}
        QTextEdit {{
            background: {FIELD}; border: 1px solid {BORDER};
            border-radius: 10px; padding: 10px;
            font-family: "{MONO}", monospace; font-size: 11px;
            color: {MUTED};
        }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{
            background: transparent; width: 9px; margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {BORDER_HI}; border-radius: 4px; min-height: 28px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QCheckBox {{ color: {TEXT}; spacing: 8px; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 5px;
            border: 1px solid {BORDER_HI}; background: {FIELD};
        }}
        QCheckBox::indicator:checked {{
            background: {ACCENT}; border: 1px solid {ACCENT};
        }}
        QDialog {{ background: {BG}; }}
        QToolTip {{
            background: {PANEL_2}; color: {TEXT};
            border: 1px solid {BORDER_HI}; padding: 6px;
        }}
        """

    # ---------------------------------------------------------- layout

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        root.addLayout(self._header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._sidebar())
        body.addLayout(self._main_column(), 1)
        root.addLayout(body, 1)

        root.addLayout(self._footer())

    def _header(self):
        bar = QHBoxLayout()
        bar.setSpacing(12)

        logo = QLabel("FFMPEG-Forge")
        logo.setStyleSheet(
            f"color:{TEXT}; font-size:19px; font-weight:700; letter-spacing:0.5px;"
        )
        bar.addWidget(logo)
        tag = QLabel("ffmpeg, without the terminal")
        tag.setStyleSheet(f"color:{DIM}; font-size:11px;")
        bar.addWidget(tag)
        bar.addStretch(1)

        self.pill = QLabel()
        self.pill.setStyleSheet(
            f"color:{MUTED}; font-size:11px; background:{FIELD};"
            f"border:1px solid {BORDER}; border-radius:11px; padding:4px 12px;"
        )
        bar.addWidget(self.pill)

        self.gear = BadgeButton("Settings")
        self.gear.setObjectName("secondary")
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.setToolTip("Settings")
        self.gear.clicked.connect(self._open_settings)
        bar.addWidget(self.gear)
        return bar

    def _sidebar(self):
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(186)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 14, 10, 14)
        lay.setSpacing(3)

        seg = QWidget()
        seg.setObjectName("segment")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(3)
        self.section_buttons = []
        for key, name in SECTIONS:
            b = QPushButton(name)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._set_section(k))
            sl.addWidget(b)
            self.section_buttons.append((key, b))
        lay.addWidget(seg)
        lay.addSpacing(12)

        lay.addWidget(label("  TASK", DIM, 10, True))
        lay.addSpacing(6)

        self.mode_buttons = []
        for key, name in MODES:
            btn = QPushButton(name)
            btn.setObjectName("mode")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            lay.addWidget(btn)
            self.mode_buttons.append((key, btn))
        lay.addStretch(1)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{DIM}; font-size:10px; padding:0 4px;")
        lay.addWidget(self.hint)
        return frame

    def _main_column(self):
        col = QVBoxLayout()
        col.setSpacing(14)

        files = Card()
        fl = QVBoxLayout(files)
        fl.setContentsMargins(14, 12, 14, 14)
        fl.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(label("FILES", MUTED, 10, True))
        head.addStretch(1)
        self.count_lb = label("", DIM, 10)
        head.addWidget(self.count_lb)
        add = QPushButton("Add")
        add.setObjectName("secondary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._browse_files)
        clear = QPushButton("Clear")
        clear.setObjectName("secondary")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear_files)
        head.addWidget(add)
        head.addWidget(clear)
        fl.addLayout(head)

        self.stack_files = QStackedWidget()
        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_files)
        self.drop.clicked.connect(self._browse_files)

        self.queue = QScrollArea()
        self.queue.setWidgetResizable(True)
        self.queue.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.queue.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self.queue_lay = QVBoxLayout(host)
        self.queue_lay.setContentsMargins(0, 0, 6, 0)
        self.queue_lay.setSpacing(6)
        self.queue_lay.addStretch(1)
        self.queue.setWidget(host)

        self.stack_files.addWidget(self.drop)
        self.stack_files.addWidget(self.queue)
        self.stack_files.setMinimumHeight(140)
        fl.addWidget(self.stack_files)
        col.addWidget(files, 2)

        preview_card = Card()
        pl = QVBoxLayout(preview_card)
        pl.setContentsMargins(0, 0, 0, 0)
        self.live_preview = LiveImagePreview()
        self.live_preview.boxChanged.connect(self._live_crop_changed)
        pl.addWidget(self.live_preview)
        col.addWidget(preview_card, 1)
        self.preview_card = preview_card

        opts = Card()
        ol = QVBoxLayout(opts)
        ol.setContentsMargins(18, 14, 18, 16)
        ol.setSpacing(12)
        self.opt_title = label("CONVERT", MUTED, 10, True)
        ol.addWidget(self.opt_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 6, 0)
        self.panels = QStackedWidget()
        self.panel_map = {}
        classes = {
            "convert": ConvertPanel, "compress": CompressPanel,
            "audio": AudioPanel, "trim": TrimPanel, "resize": ResizePanel,
            "gif": GifPanel, "frames": FramesPanel,
            "photo_convert": PhotoConvertPanel,
            "photo_resize": PhotoResizePanel,
            "photo_crop": PhotoCropPanel,
            "photo_adjust": PhotoAdjustPanel,
        }
        for key, _ in MODES:
            panel = classes[key](self.ff)
            panel.changed.connect(self._update_preview)
            self.panel_map[key] = panel
            self.panels.addWidget(panel)
        hl.addWidget(self.panels)
        hl.addStretch(1)
        scroll.setWidget(holder)
        ol.addWidget(scroll, 1)
        col.addWidget(opts, 5)

        out = Card()
        outl = QVBoxLayout(out)
        outl.setContentsMargins(14, 12, 14, 14)
        outl.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label("SAVE TO", MUTED, 10, True))
        self.out_edit = QLineEdit(self.settings.value("out_dir", "", str))
        self.out_edit.setPlaceholderText("same folder as the source file")
        self.out_edit.setMinimumHeight(32)
        self.out_edit.textChanged.connect(self._update_preview)
        pick = QPushButton("Choose")
        pick.setObjectName("secondary")
        pick.setCursor(Qt.PointingHandCursor)
        pick.clicked.connect(self._pick_out_dir)
        row.addWidget(self.out_edit, 1)
        row.addWidget(pick)
        outl.addLayout(row)

        cmd_head = QHBoxLayout()
        cmd_head.addWidget(label("COMMAND PREVIEW", MUTED, 10, True))
        cmd_head.addStretch(1)
        copy = QPushButton("Copy")
        copy.setObjectName("secondary")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(self._copy_cmd)
        cmd_head.addWidget(copy)
        outl.addLayout(cmd_head)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(66)
        outl.addWidget(self.preview)
        col.addWidget(out, 0)
        return col

    def _footer(self):
        bar = QHBoxLayout()
        bar.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.status_lb = label("Ready", MUTED, 11)
        left.addWidget(self.status_lb)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        left.addWidget(self.progress)
        bar.addLayout(left, 1)

        self.open_btn = QPushButton("Open folder")
        self.open_btn.setObjectName("secondary")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_out)
        self.open_btn.hide()
        bar.addWidget(self.open_btn)

        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.hide()
        bar.addWidget(self.cancel_btn)

        self.render_btn = QPushButton("Render")
        self.render_btn.setObjectName("primary")
        self.render_btn.setCursor(Qt.PointingHandCursor)
        self.render_btn.clicked.connect(self._start)
        bar.addWidget(self.render_btn)

        self._set_mode(
            "convert" if self.section != "photo" else "photo_convert"
        )
        return bar

    # ---------------------------------------------------------- state

    def _set_section(self, key):
        self.section = key
        for k, btn in self.section_buttons:
            btn.setChecked(k == key)
        modes = VIDEO_MODES if key == "video" else PHOTO_MODES
        allowed = [m for m, _ in modes]
        for k, btn in self.mode_buttons:
            btn.setVisible(k in allowed)
        self.settings.setValue("section", key)
        if getattr(self, "mode", None) not in allowed:
            self._set_mode(allowed[0])
        else:
            self._refresh_applicability()

    def _refresh_applicability(self):
        for row in self.rows:
            row.set_applies(
                section_accepts(self.section, row.kind), self.section
            )
        self._sync_counts()
        self._update_preview()

    def active_rows(self):
        return [r for r in self.rows if r.applies]

    def _set_mode(self, key):
        self.mode = key
        self.section = MODE_SECTION[key]
        for k, btn in self.section_buttons:
            btn.setChecked(k == self.section)
        for k, btn in self.mode_buttons:
            btn.setChecked(k == key)
            btn.setVisible(MODE_SECTION[k] == self.section)
        idx = [k for k, _ in MODES].index(key)
        self.panels.setCurrentIndex(idx)
        if hasattr(self, "_mode_fade"):
            self._mode_fade.stop()
            self._mode_fade.setStartValue(0.72)
            self._mode_fade.setEndValue(1.0)
            self._mode_fade.start()
        self.opt_title.setText(
            dict(MODES)[key].upper() + " OPTIONS"
        )
        hints = {
            "convert": "Change container or codec. Copy skips re-encoding and is instant.",
            "compress": "Target size runs two passes and lands close to the number you set.",
            "audio": "Pulls the audio track out of any video or converts audio to audio.",
            "trim": "Fast mode cuts on keyframes. Precise re-encodes for an exact cut.",
            "resize": "Scale keeps the aspect ratio. Pad and crop force the exact frame.",
            "gif": "Builds an optimized palette first, so colors stay clean.",
            "frames": "Exports stills into a folder next to the output location.",
            "photo_convert": "Change image format and quality. The size cap only shrinks.",
            "photo_resize": "Resize by long edge, one dimension, a percentage or an exact box.",
            "photo_crop": "Crop to a ratio from any anchor, or set an exact box yourself.",
            "photo_adjust": "Brightness, contrast, saturation, gamma, sharpening and rotation.",
        }
        self.hint.setText(hints.get(key, ""))
        self.preview_card.setVisible(self.section == "photo")
        self._refresh_applicability()

    def _refresh_ffmpeg_pill(self):
        if self.ff.ready:
            gpu = " · NVENC" if self.ff.has("h264_nvenc") else ""
            ver = self.ff.version.replace("ffmpeg version ", "")[:22]
            flag = " · update ready" if self.update_state.get(
                "available") else ""
            self.pill.setText(f"ffmpeg {ver}{gpu}{flag}")
            self.pill.setStyleSheet(
                f"color:{GOOD}; font-size:11px; background:{FIELD};"
                f"border:1px solid {BORDER}; border-radius:11px; padding:4px 12px;"
            )
        else:
            self.pill.setText("ffmpeg not found: open Settings")
            self.pill.setStyleSheet(
                f"color:{BAD}; font-size:11px; background:{FIELD};"
                f"border:1px solid {BAD}; border-radius:11px; padding:4px 12px;"
            )

    # ---------------------------------------------------------- files

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_files(paths)

    def _browse_files(self):
        exts = " ".join("*" + e for e in sorted(MEDIA_EXT))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add media", "", f"Media files ({exts});;All files (*)"
        )
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        expanded = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                expanded += [
                    c for c in sorted(path.iterdir())
                    if c.is_file() and c.suffix.lower() in MEDIA_EXT
                ]
            elif path.is_file():
                expanded.append(path)

        existing = {r.info.path for r in self.rows}
        added = 0
        for path in expanded:
            if path in existing:
                continue
            if path.suffix.lower() not in MEDIA_EXT:
                continue
            info = self.ff.probe(path) if self.ff.ready else MediaInfo(path=path)
            row = QueueRow(info)
            row.remove_requested.connect(self._remove_row)
            self.queue_lay.insertWidget(self.queue_lay.count() - 1, row)
            self.rows.append(row)
            added += 1

        if added:
            self.stack_files.setCurrentIndex(1)
        self._refresh_applicability()

    def _remove_row(self, row):
        self.queue_lay.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        if row in self.rows:
            self.rows.remove(row)
        if row in self.render_rows:
            self.render_rows.remove(row)
        if not self.rows:
            self.stack_files.setCurrentIndex(0)
        self._refresh_applicability()

    def _clear_files(self):
        for row in list(self.rows):
            self.queue_lay.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.rows.clear()
        self.render_rows = []
        self.stack_files.setCurrentIndex(0)
        self.progress.setValue(0)
        self.status_lb.setText("Ready")
        self.status_lb.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        self.open_btn.hide()
        self._refresh_applicability()

    def _sync_counts(self):
        n = len(self.rows)
        if not n:
            self.count_lb.setText("")
            return
        active = len(self.active_rows())
        if active == n:
            text = f"{n} file{'s' if n != 1 else ''}"
        else:
            text = f"{active} of {n} apply"
        est = self.estimate_total()
        if est:
            text += f"   ~{human_size(est)} estimated"
        self.count_lb.setText(text)

    def estimate_total(self):
        """Sum of the per-file guesses, or None if any of them is unknowable."""
        rows = self.active_rows()
        if not rows:
            return None
        try:
            opts = self.panel_map[self.mode].values()
        except Exception:
            return None
        total = 0
        for row in rows:
            got = estimate_bytes(self.mode, opts, row.info)
            if not got:
                return None
            total += got
        return total

    # ---------------------------------------------------------- output

    def _out_dir_for(self, info):
        chosen = self.out_edit.text().strip()
        return Path(chosen) if chosen else info.path.parent

    def _pick_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path:
            self.out_edit.setText(path)

    def _open_out(self):
        for row in self.render_rows or self.rows:
            if row.out_path:
                reveal(row.out_path.parent)
                return
        text = self.out_edit.text().strip()
        if text:
            reveal(Path(text))

    def _copy_cmd(self):
        QApplication.clipboard().setText(self.preview.toPlainText())
        self.status_lb.setText("Command copied to clipboard")

    def _live_crop_changed(self, x, y, w, h):
        panel = self.panel_map.get("photo_crop")
        if self.mode != "photo_crop" or not panel:
            return
        panel.aspect.setCurrentIndex(0)
        panel.cx.setValue(x); panel.cy.setValue(y); panel.cw.setValue(max(1, w)); panel.ch.setValue(max(1, h))

    def _render_live_image(self):
        if self.section != "photo" or not self.active_rows():
            self.live_preview.set_image(QImage())
            return
        src = QImage(str(self.active_rows()[0].info.path))
        if src.isNull():
            self.live_preview.set_image(QImage())
            return
        panel = self.panel_map[self.mode]; opts = panel.values(); result = src
        crop = None
        try:
            # Apply the same user-facing operations as the ffmpeg filter chain.
            # QImage keeps this local and immediate while the user drags sliders.
            if self.mode == "photo_convert":
                cap = opts.get("cap", 0)
                # Keep the interactive preview legible if a user briefly types
                # a tiny value; the export still honors the exact requested cap.
                if cap >= 64 and max(src.width(), src.height()) > cap:
                    result = src.scaled(cap, cap, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if self.mode == "photo_crop":
                if opts["ratio"] is None:
                    x,y,w,h = opts["box"]; x=max(0,min(x,src.width()-1)); y=max(0,min(y,src.height()-1)); w=min(w,src.width()-x); h=min(h,src.height()-y); crop=(x,y,w,h); result=src.copy(x,y,w,h)
                else:
                    ratio=opts["ratio"]; w=min(src.width(), int(src.height()*ratio)); h=min(src.height(), int(src.width()/ratio)); x,y=anchor_xy(opts["anchor"]); x=max(0,int((src.width()-w)/2)) if "iw" in x else (0 if x=="0" else max(0,src.width()-w)); y=max(0,int((src.height()-h)/2)) if "ih" in y else (0 if y=="0" else max(0,src.height()-h)); crop=(x,y,w,h); result=src.copy(x,y,w,h)
            elif self.mode == "photo_resize":
                b=opts["basis"]; n=opts["value"] if b in (0,1,2) else None
                if b==0: result=result.scaledToHeight(n, Qt.SmoothTransformation) if src.height()>=src.width() else result.scaledToWidth(n, Qt.SmoothTransformation)
                elif b==1: result=result.scaledToWidth(n, Qt.SmoothTransformation)
                elif b==2: result=result.scaledToHeight(n, Qt.SmoothTransformation)
                elif b==3: result=result.scaled(int(src.width()*opts["pct"]/100), int(src.height()*opts["pct"]/100), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                else:
                    bw, bh = opts["box_w"], opts["box_h"]
                    if opts["fit"] == 2:
                        result = result.scaled(bw, bh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    elif opts["fit"] == 1:
                        result = result.scaled(bw, bh, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        result = result.copy(max(0, (result.width()-bw)//2), max(0, (result.height()-bh)//2), bw, bh)
                    else:
                        result = result.scaled(bw, bh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        canvas = QImage(bw, bh, QImage.Format_RGBA8888); canvas.fill(QColor("black"))
                        painter = QPainter(canvas); painter.drawImage((bw-result.width())//2, (bh-result.height())//2, result); painter.end(); result = canvas
            elif self.mode == "photo_adjust":
                # Per-pixel preview of brightness, contrast, saturation and gamma.
                if any(abs(opts[k] - base) > 0.001 for k, base in (("brightness", 0.0), ("contrast", 1.0), ("saturation", 1.0), ("gamma", 1.0))) or opts["grayscale"]:
                    result = result.convertToFormat(QImage.Format_RGBA8888)
                    brightness, contrast, saturation, gamma = opts["brightness"] * 255.0, opts["contrast"], opts["saturation"], opts["gamma"]
                    for yy in range(result.height()):
                        for xx in range(result.width()):
                            c = result.pixelColor(xx, yy)
                            r, g, b = [((v / 255.0) ** (1.0 / gamma)) * 255.0 for v in (c.red(), c.green(), c.blue())]
                            mean = (r + g + b) / 3.0
                            r = (mean + (r - mean) * saturation - 127.5) * contrast + 127.5 + brightness
                            g = (mean + (g - mean) * saturation - 127.5) * contrast + 127.5 + brightness
                            b = (mean + (b - mean) * saturation - 127.5) * contrast + 127.5 + brightness
                            if opts["grayscale"]:
                                r = g = b = 0.299 * r + 0.587 * g + 0.114 * b
                            c.setRed(max(0, min(255, int(r)))); c.setGreen(max(0, min(255, int(g)))); c.setBlue(max(0, min(255, int(b))))
                            result.setPixelColor(xx, yy, c)
                # Sharpen is represented visibly with a lightweight edge pass.
                if opts["sharpen"]:
                    result = result.convertToFormat(QImage.Format_RGBA8888)
                    radius = {1: 0.35, 2: 0.65, 3: 1.0}[opts["sharpen"]]
                    for yy in range(1, result.height() - 1):
                        for xx in range(1, result.width() - 1):
                            c = result.pixelColor(xx, yy); n = result.pixelColor(xx-1, yy); s = result.pixelColor(xx+1, yy)
                            for getter, setter in ((lambda q: q.red(), c.setRed), (lambda q: q.green(), c.setGreen), (lambda q: q.blue(), c.setBlue)):
                                value = getter(c) + radius * (getter(c) * 2 - getter(n) - getter(s))
                                setter(max(0, min(255, int(value))))
                            result.setPixelColor(xx, yy, c)
                if opts["transform"] != "None":
                    angles={"Rotate 90° right":90,"Rotate 90° left":-90,"Rotate 180°":180}.get(opts["transform"])
                    if angles: result=result.transformed(QTransform().rotate(angles))
                    elif opts["transform"] == "Flip horizontal": result=result.mirrored(True,False)
                    elif opts["transform"] == "Flip vertical": result=result.mirrored(False,True)
        except Exception:
            result = src
        self.live_preview.set_image(src, result, crop)

    # ---------------------------------------------------------- preview

    def _current_jobs(self):
        panel = self.panel_map[self.mode]
        opts = panel.values()
        jobs = []
        for row in self.active_rows():
            info = row.info
            jobs.append(build_job(
                self.ff, self.mode, opts, info,
                self._out_dir_for(info), panel.suffix(),
                panel.extension(info),
            ))
        return jobs

    def _update_preview(self):
        if not hasattr(self, "preview"):
            return
        self._render_live_image()
        if not self.ff.ready:
            self.preview.setPlainText(
                "ffmpeg was not found. Open Settings and point FFMPEG-Forge at it."
            )
            return
        if not self.active_rows():
            self.preview.setPlainText(
                "Add a %s to see the exact command FFMPEG-Forge will run."
                % ("photo" if self.section == "photo" else "file")
            )
            return
        try:
            job = self._current_jobs()[0]
        except Exception as exc:
            self.preview.setPlainText(f"Cannot build command: {exc}")
            return
        text = "\n\n".join(pretty_cmd(c) for c in job.passes)
        extra = len(self.active_rows()) - 1
        if extra > 0:
            text += f"\n\n(and {extra} more, one per file)"
        self.preview.setPlainText(text)

    # ---------------------------------------------------------- render

    def _start(self):
        if not self.ff.ready:
            self._open_settings()
            return
        if not self.rows:
            self._browse_files()
            return
        if not self.active_rows():
            QMessageBox.information(
                self, "Nothing to do",
                "None of the queued files are %s. Switch section or add some."
                % ("photos" if self.section == "photo" else "video or audio"),
            )
            return
        if self.mode == "trim":
            opts = self.panel_map["trim"].values()
            if self.panel_map["trim"].start.text().strip() and opts["start"] is None:
                QMessageBox.warning(
                    self, "Check the start time",
                    "Use seconds, m:ss or h:mm:ss."
                )
                return
            if opts["end"] is not None and opts["start"] is not None \
                    and opts["end"] <= opts["start"]:
                QMessageBox.warning(
                    self, "Check the times", "End must come after start."
                )
                return

        import time
        jobs = self._current_jobs()
        self.render_rows = self.active_rows()
        for row in self.render_rows:
            row.set_status("queued")
            row.set_progress(0)

        self.render_btn.hide()
        self.cancel_btn.show()
        self.open_btn.hide()
        self.progress.setValue(0)
        self.render_started = time.time()
        self.status_lb.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        self.status_lb.setText("Rendering %d file(s)" % len(jobs))

        self.worker = RenderWorker(self.ff.ffmpeg, jobs, self)
        self.worker.file_progress.connect(self._on_file_progress)
        self.worker.file_stats.connect(self._on_file_stats)
        self.worker.file_status.connect(self._on_file_status)
        self.worker.total_progress.connect(self._on_total_progress)
        self.worker.finished_all.connect(self._on_done)
        self.worker.start()

    def _on_file_progress(self, idx, frac):
        if 0 <= idx < len(self.render_rows):
            self.render_rows[idx].set_progress(frac)

    def _on_file_stats(self, idx, stats):
        if 0 <= idx < len(self.render_rows):
            row = self.render_rows[idx]
            self.render_rows[idx].set_stats(
                row.bar.value() / 1000.0, stats
            )

    def _on_total_progress(self, frac):
        import time
        self.progress.setValue(int(frac * 1000))
        if frac <= 0.01 or not self.render_started:
            return
        elapsed = time.time() - self.render_started
        if elapsed < 3:
            return
        remaining = elapsed / frac - elapsed
        base = self.status_lb.text().split("   about")[0]
        self.status_lb.setText(
            "%s   about %s left" % (base, human_time(remaining))
        )

    def _on_file_status(self, idx, status, message):
        if 0 <= idx < len(self.render_rows):
            self.render_rows[idx].set_status(status, message)
            if status == "running":
                self.status_lb.setText(
                    "Rendering %s" % self.render_rows[idx].info.path.name
                )

    def _on_done(self, ok, failed):
        self.cancel_btn.hide()
        self.render_btn.show()
        self.progress.setValue(1000 if not failed else self.progress.value())
        if failed:
            self.status_lb.setText(
                f"{ok} finished, {failed} failed. Hover a red row for the reason."
            )
            self.status_lb.setStyleSheet(f"color:{WARN}; font-size:11px;")
        else:
            self.status_lb.setText(f"Done. {ok} file(s) written.")
            self.status_lb.setStyleSheet(f"color:{GOOD}; font-size:11px;")
        if ok:
            self.open_btn.show()
            if self.settings.value("open_after", True, bool):
                self._open_out()
        self.worker = None

    def _cancel(self):
        if self.worker:
            self.status_lb.setText("Stopping…")
            self.worker.cancel()

    # ---------------------------------------------------------- settings

    def _start_background_check(self):
        """Quietly ask once a day so the badge is right when Settings opens."""
        import time
        last = self.settings.value("last_check", 0.0, float)
        if time.time() - last < 86400 and self.settings.value(
                "last_latest", "", str):
            cached = parse_version(self.settings.value("last_latest", "", str))
            self.apply_update_state({
                "ok": True, "latest": cached,
                "available": newer(cached, self.ff.numeric_version),
            }, remember=False)
            return
        self.startup_check = UpdateCheck(self.ff.numeric_version, self)
        self.startup_check.done.connect(self.apply_update_state)
        self.startup_check.start()

    def apply_update_state(self, result, remember=True):
        self.update_state = dict(result)
        self.gear.set_badge(bool(result.get("available")))
        if result.get("ok") and remember:
            import time
            self.settings.setValue("last_check", time.time())
            self.settings.setValue(
                "last_latest", version_text(result.get("latest"))
            )
        self._refresh_ffmpeg_pill()

    def _open_settings(self):
        busy = bool(self.worker and self.worker.isRunning())
        dlg = SettingsDialog(
            self.ff, self.settings, self,
            last_check=self.update_state, busy=busy,
        )
        dlg.update_applied.connect(self._after_ffmpeg_change)
        try:
            if dlg.exec() == QDialog.Accepted:
                self.ff.locate(self.settings.value("ffmpeg", "", str))
                self._after_ffmpeg_change()
        finally:
            dlg.stop_threads()

    def _after_ffmpeg_change(self):
        self._refresh_ffmpeg_pill()
        self.out_edit.setText(self.settings.value("out_dir", "", str))
        if self.update_state.get("latest"):
            self.update_state["available"] = newer(
                self.update_state["latest"], self.ff.numeric_version
            )
            self.gear.set_badge(bool(self.update_state["available"]))
        self._update_preview()

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.startup_check and self.startup_check.isRunning():
            self.startup_check.wait(2000)
        e.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    font = QFont(UIFONT, 10)
    app.setFont(font)

    win = ForgeWindow()
    win.show()

    if not win.ff.ready:
        QMessageBox.information(
            win, "ffmpeg not found",
            "FFMPEG-Forge could not find ffmpeg on this machine.\n\n"
            "Open Settings and use Install ffmpeg, which downloads a verified "
            "build into the app folder. You can also point FFMPEG-Forge at an "
            "ffmpeg.exe you already have.",
        )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
