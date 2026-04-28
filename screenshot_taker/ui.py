from __future__ import annotations

import ctypes
import json
import subprocess
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QMimeData, QObject, QPoint, QRect, QSettings, QSize, QStandardPaths, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QRegion
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .browser import (
    BROWSER_CHROME,
    BROWSER_EDGE,
    ELIBRO_PAGE_FIELD_SELECTOR,
    BrowserSession,
    default_user_data_dir,
    locator_from_html_snippet,
)
from .capture import capture_screen_area, ensure_directory, export_pdf, export_zip, render_pdf_to_images, sanitize_filename
from .i18n import LANGUAGE_NAMES, Translator
from .models import CaptureArea, CaptureRecord, ElementLocator
from .ocr import (
    DEFAULT_OLLAMA_URL,
    OLLAMA_MODEL_PRESETS,
    TESSERACT_LANGUAGE_PRESETS,
    export_ollama_ocr_text_document,
    export_ollama_ocr_text_pdf,
    export_searchable_pdf_ocr,
    export_surya_ocr_text_document,
    export_surya_ocr_text_pdf,
    build_tesseract_runtime_tessdata,
    find_tesseract_executable,
    install_tesseract_languages,
    list_tesseract_languages,
    ollama_install_command,
    ollama_model_installed,
    ollama_run_command,
    pull_ollama_model,
    surya_available,
    surya_install_command,
    tesseract_install_command,
    resolve_tesseract_languages,
    test_ollama_connection,
)
from .runtime import app_icon_path, bundled_resource_path, is_frozen


def _ctrl_shift_pressed() -> bool:
    if sys.platform.startswith("win"):
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000) and bool(
            ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000
        )
    modifiers = QApplication.keyboardModifiers()
    return bool(modifiers & Qt.KeyboardModifier.ControlModifier) and bool(
        modifiers & Qt.KeyboardModifier.ShiftModifier
    )


def _format_bytes(value: int) -> str:
    size = float(max(value, 0))
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1000.0 and index < len(units) - 1:
        size /= 1000.0
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


class AreaPreviewOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._area: CaptureArea | None = None
        self._countdown_text: str = ""

    def show_area(self, area: CaptureArea) -> None:
        self._area = area
        self.setGeometry(area.x, area.y, area.width, area.height)
        self.show()
        self.raise_()
        self.update()

    def set_countdown_text(self, value: str) -> None:
        self._countdown_text = value
        if self.isVisible():
            self.update()

    def hide_overlay(self) -> None:
        self._countdown_text = ""
        self.hide()

    def clear_area(self) -> None:
        self._area = None
        self._countdown_text = ""
        self.hide()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._area is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 144, 255, 36))
        painter.setPen(QPen(QColor(30, 144, 255, 200), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self._countdown_text:
            countdown_rect = QRect(self.width() - 76, 8, 68, 24)
            painter.fillRect(countdown_rect, QColor(0, 0, 0, 132))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(
                countdown_rect,
                int(Qt.AlignmentFlag.AlignCenter),
                self._countdown_text,
            )


class AreaSelectorDialog(QDialog):
    def __init__(self, translator: Translator, mode: str, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.translator = translator
        self.mode = mode
        self.selected_area: CaptureArea | None = None
        self._anchor_global = None
        self._preview_rect = QRect()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        virtual_geometry = QApplication.primaryScreen().virtualGeometry()  # type: ignore[union-attr]
        self.setGeometry(virtual_geometry)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setModal(True)

    def _global_to_local(self, global_point):
        return global_point - self.geometry().topLeft()

    def _update_preview_from_points(self, first, second) -> None:
        first_local = self._global_to_local(first)
        second_local = self._global_to_local(second)
        self._preview_rect = QRect(first_local, second_local).normalized()
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        global_point = event.globalPosition().toPoint()
        if self.mode == "drag":
            self._anchor_global = global_point
            self._update_preview_from_points(global_point, global_point)
            return

        if self._anchor_global is None:
            self._anchor_global = global_point
            self._update_preview_from_points(global_point, global_point)
            return

        area = CaptureArea.from_points(
            (self._anchor_global.x(), self._anchor_global.y()),
            (global_point.x(), global_point.y()),
        )
        if area.is_valid():
            self.selected_area = area
            self.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._anchor_global is None:
            return
        self._update_preview_from_points(self._anchor_global, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self.mode != "drag" or self._anchor_global is None:
            return
        global_point = event.globalPosition().toPoint()
        area = CaptureArea.from_points(
            (self._anchor_global.x(), self._anchor_global.y()),
            (global_point.x(), global_point.y()),
        )
        if area.is_valid():
            self.selected_area = area
            self.accept()
        else:
            self._anchor_global = None
            self._preview_rect = QRect()
            self.update()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 42))

        if not self._preview_rect.isNull():
            painter.fillRect(self._preview_rect, QColor(30, 144, 255, 56))
            painter.setPen(QPen(QColor(30, 144, 255, 220), 2))
            painter.drawRect(self._preview_rect.adjusted(1, 1, -2, -2))
            size_text = f"{self._preview_rect.width()} x {self._preview_rect.height()}"
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(
                self._preview_rect.adjusted(8, 8, -8, -8),
                int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
                size_text,
            )

        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawText(
            self.rect().adjusted(20, 30, -20, -20),
            int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
            self.translator.text("instruction_select_area"),
        )


class AreaResizeOverlay(QWidget):
    area_changed = pyqtSignal(object)

    _BORDER_THICKNESS = 8
    _HANDLE_SIZE = 14
    _MARGIN = 14
    _MIN_SIZE = 8

    _CURSORS = {
        "n": Qt.CursorShape.SizeVerCursor,
        "s": Qt.CursorShape.SizeVerCursor,
        "e": Qt.CursorShape.SizeHorCursor,
        "w": Qt.CursorShape.SizeHorCursor,
        "move": Qt.CursorShape.SizeAllCursor,
        "ne": Qt.CursorShape.SizeBDiagCursor,
        "sw": Qt.CursorShape.SizeBDiagCursor,
        "nw": Qt.CursorShape.SizeFDiagCursor,
        "se": Qt.CursorShape.SizeFDiagCursor,
    }

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._area: CaptureArea | None = None
        self._active_handle: str | None = None
        self._press_global: QPoint | None = None
        self._origin_area: CaptureArea | None = None
        self._full_rect_drag_enabled = False
        self._modifier_timer = QTimer(self)
        self._modifier_timer.setInterval(16)
        self._modifier_timer.timeout.connect(self._refresh_interaction_mask)

    def show_area(self, area: CaptureArea) -> None:
        self._area = area
        margin = self._MARGIN
        self.setGeometry(area.x - margin, area.y - margin, area.width + margin * 2, area.height + margin * 2)
        self._refresh_interaction_mask(force=True)
        self.show()
        self.raise_()
        self._modifier_timer.start()
        self.update()

    def hide_overlay(self) -> None:
        self._active_handle = None
        self._press_global = None
        self._origin_area = None
        if self._modifier_timer.isActive():
            self._modifier_timer.stop()
        self._full_rect_drag_enabled = False
        self.unsetCursor()
        self.hide()

    def clear_area(self) -> None:
        self._area = None
        self.clearMask()
        self.hide_overlay()

    def _area_rect(self) -> QRect:
        if self._area is None:
            return QRect()
        return QRect(self._MARGIN, self._MARGIN, self._area.width, self._area.height)

    def _handle_rectangles(self) -> dict[str, QRect]:
        rect = self._area_rect()
        if rect.isNull():
            return {}

        size = self._HANDLE_SIZE
        half = size // 2
        center = rect.center()
        return {
            "nw": QRect(rect.left() - half, rect.top() - half, size, size),
            "n": QRect(center.x() - half, rect.top() - half, size, size),
            "ne": QRect(rect.right() - half, rect.top() - half, size, size),
            "e": QRect(rect.right() - half, center.y() - half, size, size),
            "se": QRect(rect.right() - half, rect.bottom() - half, size, size),
            "s": QRect(center.x() - half, rect.bottom() - half, size, size),
            "sw": QRect(rect.left() - half, rect.bottom() - half, size, size),
            "w": QRect(rect.left() - half, center.y() - half, size, size),
        }

    def _build_mask(self) -> QRegion:
        rect = self._area_rect()
        if rect.isNull():
            return QRegion()

        if self._full_rect_drag_enabled:
            ring = QRegion(rect)
        else:
            ring = QRegion(rect)
            inner = rect.adjusted(
                self._BORDER_THICKNESS,
                self._BORDER_THICKNESS,
                -self._BORDER_THICKNESS,
                -self._BORDER_THICKNESS,
            )
            if inner.width() > 0 and inner.height() > 0:
                ring = ring.subtracted(QRegion(inner))
        for handle_rect in self._handle_rectangles().values():
            ring = ring.united(QRegion(handle_rect))
        return ring

    def _refresh_interaction_mask(self, force: bool = False) -> None:
        full_rect_drag_enabled = _ctrl_shift_pressed()
        if force or full_rect_drag_enabled != self._full_rect_drag_enabled:
            self._full_rect_drag_enabled = full_rect_drag_enabled
            self.setMask(self._build_mask())
            if full_rect_drag_enabled:
                self.raise_()
            self.update()

    def _handle_at(self, position: QPoint) -> str | None:
        for name, handle_rect in self._handle_rectangles().items():
            if handle_rect.adjusted(-2, -2, 2, 2).contains(position):
                return name

        rect = self._area_rect()
        if rect.isNull():
            return None

        border = self._BORDER_THICKNESS
        edge_regions = {
            "n": QRect(rect.left(), rect.top(), rect.width(), border),
            "s": QRect(rect.left(), rect.bottom() - border + 1, rect.width(), border),
            "w": QRect(rect.left(), rect.top(), border, rect.height()),
            "e": QRect(rect.right() - border + 1, rect.top(), border, rect.height()),
        }
        for name, region in edge_regions.items():
            if region.contains(position):
                return name
        if self._full_rect_drag_enabled and rect.contains(position):
            return "move"
        return None

    def _resized_area(self, handle: str, global_point: QPoint, keep_ratio: bool) -> CaptureArea | None:
        if self._origin_area is None or self._press_global is None:
            return None

        origin = self._origin_area
        dx = global_point.x() - self._press_global.x()
        dy = global_point.y() - self._press_global.y()
        left = origin.left
        top = origin.top
        right = origin.right
        bottom = origin.bottom
        min_size = self._MIN_SIZE

        if not keep_ratio:
            if "w" in handle:
                left = min(left + dx, right - min_size)
            if "e" in handle:
                right = max(right + dx, left + min_size)
            if "n" in handle:
                top = min(top + dy, bottom - min_size)
            if "s" in handle:
                bottom = max(bottom + dy, top + min_size)
            return CaptureArea(left, top, right - left, bottom - top)

        aspect_ratio = origin.width / max(origin.height, 1)
        if aspect_ratio <= 0:
            return CaptureArea(left, top, right - left, bottom - top)

        if handle in {"nw", "ne", "sw", "se"}:
            width_delta = dx if "e" in handle else -dx
            height_delta = dy if "s" in handle else -dy
            proposed_width = max(min_size, origin.width + width_delta)
            proposed_height = max(min_size, origin.height + height_delta)
            width_change = abs(proposed_width - origin.width)
            height_change = abs(proposed_height - origin.height)
            if width_change >= height_change * aspect_ratio:
                width = int(round(proposed_width))
                height = max(min_size, int(round(width / aspect_ratio)))
            else:
                height = int(round(proposed_height))
                width = max(min_size, int(round(height * aspect_ratio)))

            if "w" in handle:
                left = origin.right - width
                right = origin.right
            else:
                left = origin.left
                right = origin.left + width

            if "n" in handle:
                top = origin.bottom - height
                bottom = origin.bottom
            else:
                top = origin.top
                bottom = origin.top + height
            return CaptureArea(left, top, right - left, bottom - top)

        if handle in {"w", "e"}:
            width_delta = dx if handle == "e" else -dx
            width = max(min_size, int(round(origin.width + width_delta)))
            height = max(min_size, int(round(width / aspect_ratio)))
            center_y = origin.top + origin.height / 2
            top = int(round(center_y - height / 2))
            bottom = top + height
            if handle == "w":
                left = origin.right - width
                right = origin.right
            else:
                left = origin.left
                right = origin.left + width
            return CaptureArea(left, top, right - left, bottom - top)

        if handle in {"n", "s"}:
            height_delta = dy if handle == "s" else -dy
            height = max(min_size, int(round(origin.height + height_delta)))
            width = max(min_size, int(round(height * aspect_ratio)))
            center_x = origin.left + origin.width / 2
            left = int(round(center_x - width / 2))
            right = left + width
            if handle == "n":
                top = origin.bottom - height
                bottom = origin.bottom
            else:
                top = origin.top
                bottom = origin.top + height
            return CaptureArea(left, top, right - left, bottom - top)

        return None

    def _moved_area(self, global_point: QPoint) -> CaptureArea | None:
        if self._origin_area is None or self._press_global is None:
            return None
        dx = global_point.x() - self._press_global.x()
        dy = global_point.y() - self._press_global.y()
        return CaptureArea(
            self._origin_area.x + dx,
            self._origin_area.y + dy,
            self._origin_area.width,
            self._origin_area.height,
        )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._area is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._area_rect()
        if self._full_rect_drag_enabled or self._active_handle == "move":
            painter.fillRect(rect, QColor(30, 144, 255, 8))
        painter.setPen(QPen(QColor(30, 144, 255, 235), 2))
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.setPen(QPen(QColor(30, 144, 255, 235), 1))
        for handle_rect in self._handle_rectangles().values():
            painter.drawRect(handle_rect.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return

        self._refresh_interaction_mask(force=True)
        handle = self._handle_at(event.position().toPoint())
        if handle is None or self._area is None:
            event.ignore()
            return

        self._active_handle = handle
        self._press_global = event.globalPosition().toPoint()
        self._origin_area = self._area
        self.setCursor(self._CURSORS[handle])
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint()
        self._refresh_interaction_mask(force=True)
        if self._active_handle is None:
            handle = self._handle_at(position)
            if handle is None:
                self.unsetCursor()
            else:
                self.setCursor(self._CURSORS[handle])
            return

        if self._active_handle == "move":
            updated_area = self._moved_area(event.globalPosition().toPoint())
            if updated_area is None:
                return
            self.show_area(updated_area)
            self.area_changed.emit(updated_area)
            return

        updated_area = self._resized_area(
            self._active_handle,
            event.globalPosition().toPoint(),
            bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
        )
        if updated_area is None or not updated_area.is_valid():
            return
        self.show_area(updated_area)
        self.area_changed.emit(updated_area)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_handle = None
            self._press_global = None
            self._origin_area = None
            handle = self._handle_at(event.position().toPoint())
            if handle is None:
                self.unsetCursor()
            else:
                self.setCursor(self._CURSORS[handle])
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._active_handle is None:
            self.unsetCursor()
        super().leaveEvent(event)


class ThumbnailListWidget(QListWidget):
    order_changed = pyqtSignal()

    _DRAG_MIME = "application/x-universal-screenshot-item"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._drag_start_position: QPoint | None = None
        self._drag_row: int | None = None

    def _target_row_for_position(self, position: QPoint) -> int:
        if self.count() == 0:
            return 0

        item = self.itemAt(position)
        if item is None:
            closest_item = None
            closest_distance = None
            for row in range(self.count()):
                candidate = self.item(row)
                rect = self.visualItemRect(candidate)
                if rect.isNull():
                    continue
                dx = 0 if rect.left() <= position.x() <= rect.right() else min(
                    abs(position.x() - rect.left()),
                    abs(position.x() - rect.right()),
                )
                dy = 0 if rect.top() <= position.y() <= rect.bottom() else min(
                    abs(position.y() - rect.top()),
                    abs(position.y() - rect.bottom()),
                )
                distance = dx + dy
                if closest_distance is None or distance < closest_distance:
                    closest_distance = distance
                    closest_item = candidate
            item = closest_item
            if item is None:
                return self.count()

        rect = self.visualItemRect(item)
        row = self.row(item)
        if rect.isNull():
            return row

        if position.y() < rect.top():
            return row
        if position.y() > rect.bottom():
            return row + 1
        if rect.height() > rect.width():
            return row + (1 if position.y() > rect.center().y() else 0)
        return row + (1 if position.x() > rect.center().x() else 0)

    def _move_item(self, source_row: int, target_row: int) -> bool:
        if source_row < 0 or source_row >= self.count():
            return False
        if target_row > source_row:
            target_row -= 1
        target_row = max(0, min(target_row, self.count() - 1))
        if target_row == source_row:
            return False

        item = self.takeItem(source_row)
        if item is None:
            return False

        self.insertItem(target_row, item)
        self.setCurrentItem(item)
        self.order_changed.emit()
        return True

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_position = event.position().toPoint()
            item = self.itemAt(self._drag_start_position)
            self._drag_row = self.row(item) if item is not None else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        if self._drag_start_position is None or self._drag_row is None:
            super().mouseMoveEvent(event)
            return

        if (event.position().toPoint() - self._drag_start_position).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        item = self.item(self._drag_row)
        if item is None:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(self._DRAG_MIME, str(self._drag_row).encode("utf-8"))
        drag.setMimeData(mime_data)
        pixmap = item.icon().pixmap(self.iconSize())
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start_position = None
        self._drag_row = None

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_start_position = None
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_row = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.source() is self and event.mimeData().hasFormat(self._DRAG_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.source() is self and event.mimeData().hasFormat(self._DRAG_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.source() is not self or not event.mimeData().hasFormat(self._DRAG_MIME):
            super().dropEvent(event)
            return

        try:
            source_row = int(bytes(event.mimeData().data(self._DRAG_MIME)).decode("utf-8"))
        except (TypeError, ValueError):
            event.ignore()
            return

        target_row = self._target_row_for_position(event.position().toPoint())
        moved = self._move_item(source_row, target_row)
        if moved:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        event.ignore()


class OcrExportWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        capture_paths: list[Path],
        target_path: Path,
        method: str,
        output_kind: str = "pdf",
        output_format: str = "pdf",
        languages: str = "eng",
        base_url: str = DEFAULT_OLLAMA_URL,
        model_name: str = "",
    ) -> None:
        super().__init__()
        self.capture_paths = capture_paths
        self.target_path = target_path
        self.method = method
        self.output_kind = output_kind
        self.output_format = output_format
        self.languages = languages
        self.base_url = base_url
        self.model_name = model_name

    def run(self) -> None:
        try:
            if self.method == "tesseract":
                if self.output_kind != "pdf":
                    raise RuntimeError("Traditional OCR export is only available as PDF.")
                path = export_searchable_pdf_ocr(
                    self.capture_paths,
                    self.target_path,
                    languages=self.languages,
                    progress_callback=self.progress.emit,
                )
            elif self.method == "ollama":
                if self.output_kind == "pdf":
                    path = export_ollama_ocr_text_pdf(
                        self.capture_paths,
                        self.target_path,
                        base_url=self.base_url,
                        model_name=self.model_name,
                        progress_callback=self.progress.emit,
                    )
                elif self.output_kind == "text":
                    path = export_ollama_ocr_text_document(
                        self.capture_paths,
                        self.target_path,
                        base_url=self.base_url,
                        model_name=self.model_name,
                        output_format=self.output_format,
                        progress_callback=self.progress.emit,
                    )
                else:
                    raise RuntimeError("Invalid OCR export kind selected.")
            elif self.method == "surya":
                if self.output_kind == "pdf":
                    path = export_surya_ocr_text_pdf(
                        self.capture_paths,
                        self.target_path,
                        progress_callback=self.progress.emit,
                    )
                elif self.output_kind == "text":
                    path = export_surya_ocr_text_document(
                        self.capture_paths,
                        self.target_path,
                        output_format=self.output_format,
                        progress_callback=self.progress.emit,
                    )
                else:
                    raise RuntimeError("Invalid OCR export kind selected.")
            else:
                raise RuntimeError("Invalid OCR method selected.")
            self.finished.emit(str(path))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    _OLLAMA_PROGRESS_SCALE = 1000

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("Codex", "UniversalScreenshotTaker")
        self.translator = Translator("en")
        driver_path = bundled_resource_path("msedgedriver.exe")
        self.browser = BrowserSession(driver_path if driver_path.exists() else None)
        self.capture_area: CaptureArea | None = None
        self.area_overlay = AreaPreviewOverlay()
        self.area_resizer = AreaResizeOverlay()
        self.area_resizer.area_changed.connect(self._area_changed_from_overlay)
        self.current_locator: ElementLocator | None = None
        self.capture_records: list[CaptureRecord] = []
        self.capture_counter = 0
        self.stop_requested = False
        self.auto_running = False
        self.picker_pending = False
        self.area_picker_pending = False
        self.current_preview_path: Path | None = None
        self.session_temp_dirs: set[Path] = set()
        self.area_picker_timer = QTimer(self)
        self.area_picker_timer.setInterval(180)
        self.area_picker_timer.timeout.connect(self._poll_picked_area)
        self._closing_in_progress = False
        self._closing_dialog: QProgressDialog | None = None
        self._ocr_export_running = False
        self._ocr_export_thread: QThread | None = None
        self._ocr_export_worker: OcrExportWorker | None = None
        self._ocr_export_dialog: QProgressDialog | None = None
        self._ocr_last_status = ""
        self._ocr_export_title = ""
        self._ocr_export_success_key = "message_export_pdf_ocr"
        self.installed_tesseract_languages = list_tesseract_languages()
        self._building_ui = True
        self._build_ui()
        self._building_ui = False
        self._load_settings()
        self._retranslate_ui()
        self._update_area_label()
        self._update_field_summary()
        self._update_mode_state()
        self._refresh_guidance()

    def _build_ui(self) -> None:
        self.setMinimumSize(1480, 900)
        self.resize(1620, 960)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(self.main_splitter)

        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setMinimumWidth(500)
        self.main_splitter.addWidget(self.controls_scroll)

        controls_container = QWidget()
        self.controls_scroll.setWidget(controls_container)
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setSpacing(10)

        self.general_group = QGroupBox()
        general_form = QFormLayout(self.general_group)
        self.language_label = QLabel()
        self.language_combo = QComboBox()
        for code in LANGUAGE_NAMES:
            self.language_combo.addItem("", code)
        self.language_combo.currentIndexChanged.connect(self._language_changed)

        self.mode_label = QLabel()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("", "automatic")
        self.mode_combo.addItem("", "semi")
        self.mode_combo.addItem("", "manual")
        self.mode_combo.currentIndexChanged.connect(self._update_mode_state)
        general_form.addRow(self.language_label, self.language_combo)
        general_form.addRow(self.mode_label, self.mode_combo)
        controls_layout.addWidget(self.general_group)

        self.browser_group = QGroupBox()
        browser_layout = QVBoxLayout(self.browser_group)
        browser_form = QFormLayout()
        self.browser_type_label = QLabel()
        self.browser_type_combo = QComboBox()
        self.browser_type_combo.addItem("", BROWSER_EDGE)
        self.browser_type_combo.addItem("", BROWSER_CHROME)
        self.browser_type_combo.currentIndexChanged.connect(self._browser_type_changed)
        self.url_label = QLabel()
        self.url_edit = QLineEdit()
        self.use_profile_checkbox = QCheckBox()
        self.use_profile_checkbox.toggled.connect(self._profile_mode_changed)
        self.user_data_dir_label = QLabel()
        self.user_data_dir_edit = QLineEdit()
        self.profile_directory_label = QLabel()
        self.profile_directory_edit = QLineEdit()
        self.use_default_profile_button = QPushButton()
        self.use_default_profile_button.clicked.connect(self._fill_default_profile_dir)
        browser_form.addRow(self.browser_type_label, self.browser_type_combo)
        browser_form.addRow(self.url_label, self.url_edit)
        browser_form.addRow(self.use_profile_checkbox)
        browser_form.addRow(self.user_data_dir_label, self.user_data_dir_edit)
        browser_form.addRow(self.profile_directory_label, self.profile_directory_edit)
        browser_layout.addLayout(browser_form)
        browser_profile_buttons = QHBoxLayout()
        browser_profile_buttons.addWidget(self.use_default_profile_button)
        browser_layout.addLayout(browser_profile_buttons)
        browser_buttons = QHBoxLayout()
        self.open_browser_button = QPushButton()
        self.close_browser_button = QPushButton()
        self.open_browser_button.clicked.connect(self._open_browser)
        self.close_browser_button.clicked.connect(self._close_browser)
        browser_buttons.addWidget(self.open_browser_button)
        browser_buttons.addWidget(self.close_browser_button)
        browser_layout.addLayout(browser_buttons)
        controls_layout.addWidget(self.browser_group)

        self.page_field_group = QGroupBox()
        field_layout = QVBoxLayout(self.page_field_group)
        field_form = QFormLayout()
        self.field_method_label = QLabel()
        self.field_method_combo = QComboBox()
        self.field_method_combo.addItem("", "preset")
        self.field_method_combo.addItem("", "css")
        self.field_method_combo.addItem("", "html")
        self.field_method_combo.addItem("", "active")
        self.field_method_combo.addItem("", "picked")
        self.field_method_combo.currentIndexChanged.connect(self._field_mode_changed)

        self.selector_label = QLabel()
        self.selector_edit = QLineEdit()
        self.html_label = QLabel()
        self.html_edit = QPlainTextEdit()
        self.html_edit.setMinimumHeight(100)
        self.field_summary_label = QLabel()
        self.field_summary_label.setWordWrap(True)
        self.field_summary_value = QLabel()
        self.field_summary_value.setWordWrap(True)

        field_form.addRow(self.field_method_label, self.field_method_combo)
        field_form.addRow(self.selector_label, self.selector_edit)
        field_form.addRow(self.html_label, self.html_edit)
        field_form.addRow(self.field_summary_label, self.field_summary_value)
        field_layout.addLayout(field_form)

        field_buttons_top = QHBoxLayout()
        self.apply_field_button = QPushButton()
        self.use_focused_button = QPushButton()
        self.apply_field_button.clicked.connect(self._apply_field)
        self.use_focused_button.clicked.connect(self._use_focused_element)
        field_buttons_top.addWidget(self.apply_field_button)
        field_buttons_top.addWidget(self.use_focused_button)
        field_layout.addLayout(field_buttons_top)

        field_buttons_bottom = QHBoxLayout()
        self.start_picker_button = QPushButton()
        self.use_picked_button = QPushButton()
        self.start_picker_button.clicked.connect(self._start_picker)
        self.use_picked_button.clicked.connect(self._use_picked_element)
        field_buttons_bottom.addWidget(self.start_picker_button)
        field_buttons_bottom.addWidget(self.use_picked_button)
        field_layout.addLayout(field_buttons_bottom)
        controls_layout.addWidget(self.page_field_group)

        self.pages_group = QGroupBox()
        pages_form = QFormLayout(self.pages_group)
        self.start_page_label = QLabel()
        self.end_page_label = QLabel()
        self.current_page_label = QLabel()
        self.wait_label = QLabel()
        self.start_page_spin = QSpinBox()
        self.end_page_spin = QSpinBox()
        self.current_page_spin = QSpinBox()
        for widget in (self.start_page_spin, self.end_page_spin, self.current_page_spin):
            widget.setRange(1, 999999)
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(0.0, 30.0)
        self.wait_spin.setDecimals(1)
        self.wait_spin.setSingleStep(0.5)
        self.wait_spin.setValue(4.0)
        self.goto_page_button = QPushButton()
        self.read_current_page_button = QPushButton()
        self.goto_page_button.clicked.connect(self._go_to_current_page)
        self.read_current_page_button.clicked.connect(self._read_current_page)
        pages_form.addRow(self.start_page_label, self.start_page_spin)
        pages_form.addRow(self.end_page_label, self.end_page_spin)
        pages_form.addRow(self.current_page_label, self.current_page_spin)
        pages_form.addRow(self.wait_label, self.wait_spin)
        pages_buttons = QHBoxLayout()
        pages_buttons.addWidget(self.goto_page_button)
        pages_buttons.addWidget(self.read_current_page_button)
        pages_form.addRow(pages_buttons)
        controls_layout.addWidget(self.pages_group)

        self.capture_group = QGroupBox()
        capture_layout = QVBoxLayout(self.capture_group)
        capture_form = QFormLayout()
        self.area_status_label = QLabel()
        self.area_value_label = QLabel()
        self.area_value_label.setWordWrap(True)
        self.selection_method_label = QLabel()
        self.selection_method_combo = QComboBox()
        self.selection_method_combo.addItem("", "drag")
        self.selection_method_combo.addItem("", "corners")
        self.selection_method_combo.addItem("", "browser")
        capture_form.addRow(self.area_status_label, self.area_value_label)
        capture_form.addRow(self.selection_method_label, self.selection_method_combo)
        capture_layout.addLayout(capture_form)

        capture_buttons = QHBoxLayout()
        self.start_area_selection_button = QPushButton()
        self.start_area_selection_button.clicked.connect(self._start_area_selection)
        capture_buttons.addWidget(self.start_area_selection_button)
        capture_layout.addLayout(capture_buttons)

        overlay_buttons = QHBoxLayout()
        self.toggle_overlay_button = QPushButton()
        self.clear_area_button = QPushButton()
        self.toggle_overlay_button.clicked.connect(self._toggle_overlay)
        self.clear_area_button.clicked.connect(self._clear_area)
        overlay_buttons.addWidget(self.toggle_overlay_button)
        overlay_buttons.addWidget(self.clear_area_button)
        capture_layout.addLayout(overlay_buttons)

        self.reuse_area_checkbox = QCheckBox()
        self.reuse_area_checkbox.setChecked(True)
        self.reselect_area_checkbox = QCheckBox()
        self.restore_area_on_startup_checkbox = QCheckBox()
        capture_layout.addWidget(self.reuse_area_checkbox)
        capture_layout.addWidget(self.reselect_area_checkbox)
        capture_layout.addWidget(self.restore_area_on_startup_checkbox)
        controls_layout.addWidget(self.capture_group)

        self.output_group = QGroupBox()
        output_layout = QVBoxLayout(self.output_group)
        output_form = QFormLayout()
        self.output_dir_label = QLabel()
        self.output_dir_edit = QLineEdit()
        self.base_name_label = QLabel()
        self.base_name_edit = QLineEdit()
        self.base_name_edit.editingFinished.connect(self._ensure_base_name)
        output_form.addRow(self.output_dir_label, self.output_dir_edit)
        output_form.addRow(self.base_name_label, self.base_name_edit)
        output_layout.addLayout(output_form)
        output_buttons = QHBoxLayout()
        self.browse_output_button = QPushButton()
        self.open_folder_button = QPushButton()
        self.import_pdf_button = QPushButton()
        self.browse_output_button.clicked.connect(self._browse_output_dir)
        self.open_folder_button.clicked.connect(self._open_output_dir)
        self.import_pdf_button.clicked.connect(self._import_pdf_pages)
        output_buttons.addWidget(self.browse_output_button)
        output_buttons.addWidget(self.open_folder_button)
        output_buttons.addWidget(self.import_pdf_button)
        output_layout.addLayout(output_buttons)
        controls_layout.addWidget(self.output_group)

        self.ocr_group = QGroupBox()
        ocr_layout = QVBoxLayout(self.ocr_group)
        ocr_form = QFormLayout()
        self.ocr_method_label = QLabel()
        self.ocr_method_combo = QComboBox()
        self.ocr_method_combo.addItem("", "tesseract")
        self.ocr_method_combo.addItem("", "ollama")
        self.ocr_method_combo.addItem("", "surya")
        self.ocr_method_combo.currentIndexChanged.connect(self._update_ocr_state)
        self.ocr_language_label = QLabel()
        self.ocr_language_combo = QComboBox()
        self.ocr_language_combo.setEditable(True)
        self.ocr_language_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ocr_language_combo.currentTextChanged.connect(self._refresh_tesseract_language_state)
        self.ollama_url_label = QLabel()
        self.ollama_url_edit = QLineEdit(DEFAULT_OLLAMA_URL)
        self.ollama_model_label = QLabel()
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for preset in OLLAMA_MODEL_PRESETS:
            self.ollama_model_combo.addItem("", preset.model)
        self.ollama_model_combo.currentIndexChanged.connect(self._update_ollama_model_hint)
        self.ollama_model_combo.editTextChanged.connect(self._update_ollama_model_hint)
        self.ocr_language_hint = QLabel()
        self.ocr_language_hint.setWordWrap(True)
        self.tesseract_language_state_label = QLabel()
        self.tesseract_language_state_label.setWordWrap(True)
        self.ollama_model_hint_label = QLabel()
        self.ollama_model_hint_value = QLabel()
        self.ollama_model_hint_value.setWordWrap(True)
        ocr_form.addRow(self.ocr_method_label, self.ocr_method_combo)
        ocr_form.addRow(self.ocr_language_label, self.ocr_language_combo)
        ocr_form.addRow(self.ollama_url_label, self.ollama_url_edit)
        ocr_form.addRow(self.ollama_model_label, self.ollama_model_combo)
        ocr_form.addRow(self.ollama_model_hint_label, self.ollama_model_hint_value)
        ocr_layout.addLayout(ocr_form)
        ocr_layout.addWidget(self.ocr_language_hint)
        ocr_layout.addWidget(self.tesseract_language_state_label)
        ocr_buttons = QHBoxLayout()
        self.install_tesseract_language_button = QPushButton()
        self.install_tesseract_language_button.clicked.connect(self._install_selected_tesseract_languages)
        self.pull_ollama_model_button = QPushButton()
        self.test_ollama_button = QPushButton()
        self.install_surya_button = QPushButton()
        ocr_buttons.addWidget(self.install_tesseract_language_button)
        self.pull_ollama_model_button.clicked.connect(self._pull_selected_ollama_model)
        self.test_ollama_button.clicked.connect(self._test_selected_ollama_model)
        self.install_surya_button.clicked.connect(self._install_surya)
        ocr_buttons.addWidget(self.pull_ollama_model_button)
        ocr_buttons.addWidget(self.test_ollama_button)
        ocr_buttons.addWidget(self.install_surya_button)
        ocr_layout.addLayout(ocr_buttons)
        self.export_pdf_ocr_button = QPushButton()
        self.export_pdf_ocr_button.clicked.connect(self._export_pdf_with_ocr)
        ocr_layout.addWidget(self.export_pdf_ocr_button)
        self.export_ocr_document_button = QPushButton()
        self.export_ocr_document_button.clicked.connect(self._export_ocr_document)
        ocr_layout.addWidget(self.export_ocr_document_button)
        self.ocr_export_note_label = QLabel()
        self.ocr_export_note_label.setWordWrap(True)
        ocr_layout.addWidget(self.ocr_export_note_label)
        controls_layout.addWidget(self.ocr_group)

        self.actions_group = QGroupBox()
        actions_layout = QVBoxLayout(self.actions_group)
        self.capture_now_button = QPushButton()
        self.capture_next_button = QPushButton()
        self.next_page_button = QPushButton()
        self.run_auto_button = QPushButton()
        self.stop_button = QPushButton()
        self.start_over_button = QPushButton()
        self.export_zip_button = QPushButton()
        self.export_pdf_button = QPushButton()

        self.capture_now_button.clicked.connect(self._capture_now)
        self.capture_next_button.clicked.connect(self._capture_and_next)
        self.next_page_button.clicked.connect(self._next_page_only)
        self.run_auto_button.clicked.connect(self._run_automatic_range)
        self.stop_button.clicked.connect(self._stop_run)
        self.start_over_button.clicked.connect(self._start_over_session)
        self.export_zip_button.clicked.connect(self._export_zip)
        self.export_pdf_button.clicked.connect(self._export_pdf)

        for button in (
            self.capture_now_button,
            self.capture_next_button,
            self.next_page_button,
            self.run_auto_button,
            self.stop_button,
            self.start_over_button,
            self.export_zip_button,
            self.export_pdf_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            actions_layout.addWidget(button)
        controls_layout.addWidget(self.actions_group)
        controls_layout.addStretch(1)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setStretchFactor(1, 1)

        self.status_group = QGroupBox()
        status_layout = QFormLayout(self.status_group)
        self.operation_label = QLabel()
        self.operation_value = QLabel()
        self.operation_value.setWordWrap(True)
        self.instruction_label = QLabel()
        self.instruction_value = QLabel()
        self.instruction_value.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        status_layout.addRow(self.operation_label, self.operation_value)
        status_layout.addRow(self.instruction_label, self.instruction_value)
        status_layout.addRow(self.progress_bar)
        self.right_splitter.addWidget(self.status_group)

        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)

        self.preview_group = QGroupBox()
        preview_group_layout = QVBoxLayout(self.preview_group)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(260)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        preview_group_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview_group)

        self.gallery_group = QGroupBox()
        gallery_layout = QVBoxLayout(self.gallery_group)
        self.gallery_list = ThumbnailListWidget()
        self.gallery_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery_list.setIconSize(QSize(136, 136))
        self.gallery_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery_list.setMovement(QListWidget.Movement.Static)
        self.gallery_list.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.gallery_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.gallery_list.setDragEnabled(True)
        self.gallery_list.setAcceptDrops(True)
        self.gallery_list.viewport().setAcceptDrops(True)
        self.gallery_list.setDropIndicatorShown(True)
        self.gallery_list.setDragDropOverwriteMode(False)
        self.gallery_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.gallery_list.setSpacing(8)
        self.gallery_list.setGridSize(QSize(156, 188))
        self.gallery_list.setWrapping(True)
        self.gallery_list.currentItemChanged.connect(self._update_preview_from_selection)
        self.gallery_list.customContextMenuRequested.connect(self._show_gallery_context_menu)
        self.gallery_list.itemDoubleClicked.connect(self._open_capture_in_viewer)
        self.gallery_list.order_changed.connect(self._sync_capture_records_from_gallery)
        gallery_layout.addWidget(self.gallery_list)
        preview_layout.addWidget(self.gallery_group)
        preview_container.setMinimumWidth(780)
        self.right_splitter.addWidget(preview_container)

        self.log_group = QGroupBox()
        log_layout = QVBoxLayout(self.log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        self.right_splitter.addWidget(self.log_group)
        self.right_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([560, 1040])
        self.right_splitter.setSizes([170, 590, 220])

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self._tr("window_title"))
        self.general_group.setTitle(self._tr("general_group"))
        self.language_label.setText(self._tr("language"))
        self.mode_label.setText(self._tr("capture_mode"))
        self.browser_group.setTitle(self._tr("browser_group"))
        self.browser_type_label.setText(self._tr("browser_type"))
        self.url_label.setText(self._tr("url"))
        self.use_profile_checkbox.setText(self._tr("use_profile_data"))
        self.user_data_dir_label.setText(self._tr("user_data_dir"))
        self.profile_directory_label.setText(self._tr("profile_directory"))
        self.use_default_profile_button.setText(self._tr("use_default_data_dir"))
        self.open_browser_button.setText(self._tr("open_browser"))
        self.close_browser_button.setText(self._tr("close_browser"))

        self.page_field_group.setTitle(self._tr("page_field_group"))
        self.field_method_label.setText(self._tr("field_method"))
        self.selector_label.setText(self._tr("css_selector"))
        self.html_label.setText(self._tr("html_snippet"))
        self.field_summary_label.setText(self._tr("field_status"))
        self.apply_field_button.setText(self._tr("apply_field"))
        self.use_focused_button.setText(self._tr("use_focused"))
        self.start_picker_button.setText(self._tr("start_picker"))
        self.use_picked_button.setText(self._tr("use_picked"))

        self.pages_group.setTitle(self._tr("pages_group"))
        self.start_page_label.setText(self._tr("start_page"))
        self.end_page_label.setText(self._tr("end_page"))
        self.current_page_label.setText(self._tr("current_page"))
        self.wait_label.setText(self._tr("wait_seconds"))
        self.goto_page_button.setText(self._tr("goto_page"))
        self.read_current_page_button.setText(self._tr("read_current_page"))

        self.capture_group.setTitle(self._tr("capture_group"))
        self.area_status_label.setText(self._tr("area_status"))
        self.selection_method_label.setText(self._tr("selection_method"))
        self.start_area_selection_button.setText(self._tr("start_area_selection"))
        self.clear_area_button.setText(self._tr("clear_area"))
        self.reuse_area_checkbox.setText(self._tr("reuse_area"))
        self.reselect_area_checkbox.setText(self._tr("reselect_area"))
        self.restore_area_on_startup_checkbox.setText(self._tr("restore_area_on_startup"))

        self.output_group.setTitle(self._tr("output_group"))
        self.output_dir_label.setText(self._tr("output_dir"))
        self.base_name_label.setText(self._tr("base_name"))
        self.browse_output_button.setText(self._tr("browse"))
        self.open_folder_button.setText(self._tr("open_folder"))
        self.import_pdf_button.setText(self._tr("import_pdf"))

        self.ocr_group.setTitle(self._tr("ocr_group"))
        self.ocr_method_label.setText(self._tr("ocr_method"))
        self.ocr_language_label.setText(self._tr("ocr_languages"))
        self.ocr_language_hint.setText(self._tr("ocr_languages_hint"))
        self.install_tesseract_language_button.setText(self._tr("install_tesseract_languages"))
        self.ollama_url_label.setText(self._tr("ollama_url"))
        self.ollama_model_label.setText(self._tr("ollama_model"))
        self.ollama_model_hint_label.setText(self._tr("ollama_model_notes"))
        self.pull_ollama_model_button.setText(self._tr("pull_ollama_model"))
        self.test_ollama_button.setText(self._tr("test_ollama"))
        self.install_surya_button.setText(self._tr("install_surya"))
        self._update_ocr_action_texts()

        self.actions_group.setTitle(self._tr("actions_group"))
        self.capture_now_button.setText(self._tr("capture_now"))
        self.capture_next_button.setText(self._tr("capture_and_next"))
        self.next_page_button.setText(self._tr("next_page"))
        self.run_auto_button.setText(self._tr("run_automatic"))
        self.stop_button.setText(self._tr("stop"))
        self.start_over_button.setText(self._tr("start_over"))
        self.export_zip_button.setText(self._tr("export_zip"))
        self.export_pdf_button.setText(self._tr("export_pdf"))

        self.status_group.setTitle(self._tr("status_group"))
        self.operation_label.setText(self._tr("current_operation"))
        self.instruction_label.setText(self._tr("next_step"))
        self.preview_group.setTitle(self._tr("preview_group"))
        self.gallery_group.setTitle(self._tr("gallery_group"))
        self.log_group.setTitle(self._tr("log_group"))

        self.mode_combo.setItemText(0, self._tr("mode_automatic"))
        self.mode_combo.setItemText(1, self._tr("mode_semi"))
        self.mode_combo.setItemText(2, self._tr("mode_manual"))
        self.browser_type_combo.setItemText(0, self._tr("browser_edge"))
        self.browser_type_combo.setItemText(1, self._tr("browser_chrome"))

        self.field_method_combo.setItemText(0, self._tr("field_method_preset"))
        self.field_method_combo.setItemText(1, self._tr("field_method_css"))
        self.field_method_combo.setItemText(2, self._tr("field_method_html"))
        self.field_method_combo.setItemText(3, self._tr("field_method_active"))
        self.field_method_combo.setItemText(4, self._tr("field_method_picked"))
        self.selection_method_combo.setItemText(0, self._tr("selection_method_drag"))
        self.selection_method_combo.setItemText(1, self._tr("selection_method_corners"))
        self.selection_method_combo.setItemText(2, self._tr("selection_method_browser"))
        self.ocr_method_combo.setItemText(0, self._tr("ocr_method_tesseract"))
        self.ocr_method_combo.setItemText(1, self._tr("ocr_method_ollama"))
        self.ocr_method_combo.setItemText(2, self._tr("ocr_method_surya"))
        for index, preset in enumerate(OLLAMA_MODEL_PRESETS):
            label = f"{preset.label} - {preset.model}"
            if preset.recommended:
                label = f"{label} ({self._tr('recommended')})"
            self.ollama_model_combo.setItemText(index, label)

        for index, code in enumerate(LANGUAGE_NAMES):
            self.language_combo.setItemText(index, LANGUAGE_NAMES[code])

        self.selector_edit.setPlaceholderText(ELIBRO_PAGE_FIELD_SELECTOR)
        self.html_edit.setPlaceholderText('<input aria-label="Jump to Page" ... >')
        self._populate_tesseract_language_combo()
        self.ocr_language_combo.setToolTip(self._tr("ocr_languages_hint"))
        if self.ocr_language_combo.lineEdit() is not None:
            self.ocr_language_combo.lineEdit().setPlaceholderText(self._tr("ocr_languages_placeholder"))
            self.ocr_language_combo.lineEdit().setToolTip(self._tr("ocr_languages_hint"))
        if self.ollama_model_combo.lineEdit() is not None:
            self.ollama_model_combo.lineEdit().setPlaceholderText(self._tr("ollama_model_placeholder"))
        self._update_field_summary()
        self._update_area_label()
        self._update_toggle_overlay_text()
        self._update_ollama_model_hint()
        self._refresh_tesseract_language_state()
        self._update_ocr_state()
        self._refresh_guidance()

    def _tr(self, key: str, **kwargs: object) -> str:
        return self.translator.text(key, **kwargs)

    def _language_changed(self, *_args) -> None:
        self.translator.set_language(self.language_combo.currentData())
        self._retranslate_ui()

    def _browser_type_changed(self, *_args) -> None:
        if self.use_profile_checkbox.isChecked() and not self.user_data_dir_edit.text().strip():
            self._fill_default_profile_dir()

    def _profile_mode_changed(self, *_args) -> None:
        enabled = self.use_profile_checkbox.isChecked()
        self.user_data_dir_edit.setEnabled(enabled)
        self.profile_directory_edit.setEnabled(enabled)
        self.use_default_profile_button.setEnabled(enabled)
        self._refresh_guidance()

    def _fill_default_profile_dir(self) -> None:
        default_dir = default_user_data_dir(self.browser_type_combo.currentData())
        if default_dir:
            self.user_data_dir_edit.setText(str(default_dir))
        if not self.profile_directory_edit.text().strip():
            self.profile_directory_edit.setText("Default")

    def _default_output_dir(self) -> Path:
        pictures_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        return Path(pictures_dir or str(Path.home())) / "UniversalScreenshotTaker"

    def _temp_root_dir(self) -> Path:
        temp_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
        return Path(temp_location or tempfile.gettempdir())

    def _is_transient_temp_path(self, path: Path) -> bool:
        try:
            resolved_path = path.resolve()
            temp_root = self._temp_root_dir().resolve()
        except OSError:
            return False
        return resolved_path == temp_root or temp_root in resolved_path.parents

    def _normalized_output_dir(self, value: str) -> Path:
        candidate = Path(value) if value else self._default_output_dir()
        if self._is_transient_temp_path(candidate):
            return self._default_output_dir()
        return candidate

    def _default_export_dir(self) -> Path:
        saved_export_dir = str(self.settings.value("last_export_dir", ""))
        candidate = Path(saved_export_dir) if saved_export_dir else self._default_output_dir()
        if self._is_transient_temp_path(candidate):
            return self._default_output_dir()
        return candidate

    def _remember_export_dir(self, target: str) -> None:
        self.settings.setValue("last_export_dir", str(Path(target).parent))

    def _open_export_folder_for_path(self, target: str | Path) -> None:
        folder = Path(target).expanduser().resolve().parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _default_base_name(self) -> str:
        return "document_capture"

    def _ensure_base_name(self) -> str:
        raw_name = self.base_name_edit.text().strip()
        sanitized = sanitize_filename(raw_name) if raw_name else ""
        if not sanitized:
            sanitized = self._default_base_name()
        if self.base_name_edit.text().strip() != sanitized:
            self.base_name_edit.setText(sanitized)
        return sanitized

    def _page_range_suffix(self) -> str:
        page_numbers = sorted({record.page_number for record in self.capture_records if record.page_number is not None})
        if not page_numbers:
            return ""

        ranges: list[str] = []
        start = page_numbers[0]
        end = start
        for page_number in page_numbers[1:]:
            if page_number == end + 1:
                end = page_number
                continue
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = end = page_number
        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")
        return "_P" + "_".join(ranges)

    def _export_base_name(self) -> str:
        return self._ensure_base_name()

    def _export_file_stem(self, suffix: str) -> str:
        return f"{self._export_base_name()}_{suffix}{self._page_range_suffix()}"

    def _ocr_pdf_default_name(self) -> str:
        method = str(self.ocr_method_combo.currentData() or "").strip()
        if method == "ollama":
            return self._export_file_stem("PDF_AI_OCR") + ".pdf"
        if method == "surya":
            return self._export_file_stem("PDF_SURYA_OCR") + ".pdf"
        return self._export_file_stem("PDF_OCR") + ".pdf"

    def _ocr_pdf_title_key(self) -> str:
        method = str(self.ocr_method_combo.currentData() or "").strip()
        if method == "ollama":
            return "export_ai_ocr_pdf"
        if method == "surya":
            return "export_surya_ocr_pdf"
        return "export_pdf_ocr"

    def _ocr_pdf_success_key(self) -> str:
        method = str(self.ocr_method_combo.currentData() or "").strip()
        if method == "ollama":
            return "message_export_ai_ocr_pdf"
        if method == "surya":
            return "message_export_surya_ocr_pdf"
        return "message_export_pdf_ocr"

    def _update_ocr_action_texts(self) -> None:
        title_key = self._ocr_pdf_title_key()
        self.export_pdf_ocr_button.setText(self._tr(title_key))
        document_key = "export_surya_ocr_document" if title_key == "export_surya_ocr_pdf" else "export_ocr_document"
        self.export_ocr_document_button.setText(self._tr(document_key))
        if title_key == "export_ai_ocr_pdf":
            note_key = "ocr_export_note_ollama"
        elif title_key == "export_surya_ocr_pdf":
            note_key = "ocr_export_note_surya"
        else:
            note_key = "ocr_export_note_tesseract"
        self.ocr_export_note_label.setText(self._tr(note_key))

    def _ocr_text_default_name(self, extension: str) -> str:
        normalized_extension = extension.strip().lower().lstrip(".") or "txt"
        method = str(self.ocr_method_combo.currentData() or "").strip()
        if method == "surya":
            return self._export_file_stem("SURYA_OCR") + f".{normalized_extension}"
        if method == "ollama":
            return self._export_file_stem("AI_OCR") + f".{normalized_extension}"
        return self._export_file_stem("OCR") + f".{normalized_extension}"

    def _ocr_text_export_extension(self, target: str, selected_filter: str) -> str:
        filter_map = {
            "Text (*.txt)": "txt",
            "Markdown (*.md)": "md",
            "HTML (*.html)": "html",
            "EPUB (*.epub)": "epub",
            "DOCX (*.docx)": "docx",
        }
        suffix = Path(target).suffix.strip().lower().lstrip(".")
        if suffix in {"txt", "md", "markdown", "html", "epub", "docx"}:
            return "md" if suffix == "markdown" else suffix
        return filter_map.get(selected_filter, "txt")

    def _selected_capture_paths(self) -> list[Path]:
        return [record.file_path for record in self.capture_records]

    def _ollama_url(self) -> str:
        value = self.ollama_url_edit.text().strip() or DEFAULT_OLLAMA_URL
        self.ollama_url_edit.setText(value.rstrip("/") or DEFAULT_OLLAMA_URL)
        return self.ollama_url_edit.text().strip()

    def _current_ollama_model_tag(self) -> str:
        text_value = self.ollama_model_combo.currentText().strip()
        current_index = self.ollama_model_combo.currentIndex()
        if current_index >= 0:
            current_label = self.ollama_model_combo.itemText(current_index).strip()
            current_data = str(self.ollama_model_combo.itemData(current_index) or "").strip()
            if not text_value or text_value == current_label:
                return current_data
            if text_value == current_data:
                return text_value
        return text_value

    def _selected_ollama_model(self) -> str:
        model_name = self._current_ollama_model_tag()
        if not model_name:
            raise RuntimeError(self._tr("error_ollama_model_required"))
        return model_name

    def _tesseract_language_label(self, code: str) -> str:
        key_map = {preset_code: label_key for preset_code, label_key in TESSERACT_LANGUAGE_PRESETS}
        label_key = key_map.get(code)
        if label_key is None:
            return code
        return f"{self._tr(label_key)} ({code})"

    def _selected_tesseract_languages(self) -> str:
        raw_value = self.ocr_language_combo.currentText().strip()
        if raw_value:
            if " (" in raw_value and ")" in raw_value:
                code_start = raw_value.rfind("(")
                code_end = raw_value.find(")", code_start)
                if code_start >= 0 and code_end > code_start:
                    code_value = raw_value[code_start + 1 : code_end].strip()
                    if code_value:
                        return code_value
            return raw_value

        current_data = self.ocr_language_combo.currentData()
        if current_data is not None:
            value = str(current_data).strip()
            if value:
                return value
        return "eng"

    def _requested_tesseract_language_codes(self) -> list[str]:
        selected_value = self._selected_tesseract_languages()
        values: list[str] = []
        seen: set[str] = set()
        for part in selected_value.replace(",", "+").split("+"):
            code = part.strip()
            if code and code not in seen:
                seen.add(code)
                values.append(code)
        return values or ["eng"]

    def _refresh_tesseract_installed_languages(self) -> bool:
        refreshed_languages = list_tesseract_languages()
        if refreshed_languages == self.installed_tesseract_languages:
            return False
        self.installed_tesseract_languages = refreshed_languages
        self._populate_tesseract_language_combo(self._selected_tesseract_languages())
        return True

    def _analyze_tesseract_languages(self) -> tuple[str, list[str], str, list[str]]:
        self._refresh_tesseract_installed_languages()
        selected_value = self._selected_tesseract_languages()
        requested_codes = self._requested_tesseract_language_codes()
        effective_value, missing_languages = resolve_tesseract_languages(
            selected_value,
            self.installed_tesseract_languages,
        )
        return selected_value, requested_codes, effective_value or "eng", missing_languages

    def _refresh_tesseract_language_state(self, *_args) -> None:
        selected_value, _requested_codes, effective_value, missing_languages = self._analyze_tesseract_languages()
        available_text = ", ".join(self.installed_tesseract_languages) or self._tr("none")
        if find_tesseract_executable() is None:
            self.tesseract_language_state_label.setText(
                self._tr("tesseract_engine_missing_hint", command=tesseract_install_command())
            )
            self.install_tesseract_language_button.setEnabled(False)
            return
        if missing_languages:
            self.tesseract_language_state_label.setText(
                self._tr(
                    "tesseract_languages_missing",
                    requested=selected_value,
                    missing=", ".join(missing_languages),
                    effective=effective_value,
                    available=available_text,
                )
            )
        else:
            self.tesseract_language_state_label.setText(
                self._tr("tesseract_languages_ready", selected=selected_value or "eng", available=available_text)
            )
        use_tesseract = self.ocr_method_combo.currentData() == "tesseract" and not self._ocr_export_running
        self.install_tesseract_language_button.setEnabled(use_tesseract)

    def _populate_tesseract_language_combo(self, selected_value: str | None = None) -> None:
        current_value = (selected_value or self._selected_tesseract_languages()).strip() or "eng"
        previous_block = self.ocr_language_combo.blockSignals(True)
        self.ocr_language_combo.clear()

        seen_codes: set[str] = set()
        for code, _label_key in TESSERACT_LANGUAGE_PRESETS:
            self.ocr_language_combo.addItem(self._tesseract_language_label(code), code)
            seen_codes.add(code)

        for code in self.installed_tesseract_languages:
            if code not in seen_codes:
                self.ocr_language_combo.addItem(code, code)
                seen_codes.add(code)

        selected_index = self.ocr_language_combo.findData(current_value)
        if selected_index >= 0:
            self.ocr_language_combo.setCurrentIndex(selected_index)
        else:
            self.ocr_language_combo.setEditText(current_value)
        self.ocr_language_combo.blockSignals(previous_block)

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return
        if combo.isEditable():
            combo.setEditText(value)

    def _load_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self._set_combo_by_data(self.language_combo, str(self.settings.value("language", "en")))
        self._set_combo_by_data(self.browser_type_combo, str(self.settings.value("browser_type", BROWSER_EDGE)))
        self.url_edit.setText(str(self.settings.value("url", "")))
        self.use_profile_checkbox.setChecked(str(self.settings.value("use_profile_data", "true")).lower() == "true")
        saved_user_data_dir = str(self.settings.value("user_data_dir", ""))
        self.user_data_dir_edit.setText(saved_user_data_dir)
        self.profile_directory_edit.setText(str(self.settings.value("profile_directory", "Default")))
        self.output_dir_edit.setText(str(self._normalized_output_dir(str(self.settings.value("output_dir", "")))))
        saved_base_name = str(self.settings.value("base_name", self._default_base_name()))
        if sanitize_filename(saved_base_name) == "capture":
            saved_base_name = self._default_base_name()
        self.base_name_edit.setText(saved_base_name)
        self._ensure_base_name()
        self._set_combo_by_data(self.ocr_method_combo, str(self.settings.value("ocr_method", "tesseract")))
        self._populate_tesseract_language_combo(str(self.settings.value("ocr_languages", "eng")))
        self.ollama_url_edit.setText(str(self.settings.value("ollama_url", DEFAULT_OLLAMA_URL)))
        self._set_combo_by_data(self.ollama_model_combo, str(self.settings.value("ollama_model", OLLAMA_MODEL_PRESETS[0].model)))

        self._set_combo_by_data(self.mode_combo, str(self.settings.value("mode", "automatic")))
        self._set_combo_by_data(self.field_method_combo, str(self.settings.value("field_mode", "preset")))
        self._set_combo_by_data(self.selection_method_combo, str(self.settings.value("selection_method", "drag")))
        self.selector_edit.setText(str(self.settings.value("selector_text", ELIBRO_PAGE_FIELD_SELECTOR)))
        self.html_edit.setPlainText(str(self.settings.value("html_snippet", "")))

        self.start_page_spin.setValue(int(self.settings.value("start_page", 1)))
        self.end_page_spin.setValue(int(self.settings.value("end_page", 10)))
        self.current_page_spin.setValue(1)
        self.wait_spin.setValue(float(self.settings.value("wait_seconds", 4.0)))
        self.reuse_area_checkbox.setChecked(str(self.settings.value("reuse_area", "true")).lower() == "true")
        self.reselect_area_checkbox.setChecked(str(self.settings.value("reselect_area", "false")).lower() == "true")
        self.restore_area_on_startup_checkbox.setChecked(
            str(self.settings.value("restore_area_on_startup", "true")).lower() == "true"
        )

        locator_payload = str(self.settings.value("locator", ""))
        if locator_payload:
            try:
                self.current_locator = ElementLocator.from_dict(json.loads(locator_payload))
            except json.JSONDecodeError:
                self.current_locator = None

        area_payload = str(self.settings.value("area", ""))
        if area_payload and self.restore_area_on_startup_checkbox.isChecked():
            try:
                self.capture_area = CaptureArea.from_dict(json.loads(area_payload))
            except json.JSONDecodeError:
                self.capture_area = None

        overlay_enabled = str(self.settings.value("overlay_enabled", "true")).lower() == "true"
        if self.restore_area_on_startup_checkbox.isChecked() and overlay_enabled and self.capture_area:
            self._show_area_overlays(self.capture_area)

        main_splitter_state = self.settings.value("main_splitter_state")
        if main_splitter_state:
            self.main_splitter.restoreState(main_splitter_state)
        right_splitter_state = self.settings.value("right_splitter_state")
        if right_splitter_state:
            self.right_splitter.restoreState(right_splitter_state)

        if self.use_profile_checkbox.isChecked() and not saved_user_data_dir:
            self._fill_default_profile_dir()
        self._field_mode_changed()
        self._profile_mode_changed()

    def _save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("language", self.language_combo.currentData())
        self.settings.setValue("browser_type", self.browser_type_combo.currentData())
        self.settings.setValue("url", self.url_edit.text().strip())
        self.settings.setValue("use_profile_data", self.use_profile_checkbox.isChecked())
        self.settings.setValue("user_data_dir", self.user_data_dir_edit.text().strip())
        self.settings.setValue("profile_directory", self.profile_directory_edit.text().strip())
        self.settings.setValue("output_dir", self.output_dir_edit.text().strip())
        self.settings.setValue("base_name", self._ensure_base_name())
        self.settings.setValue("ocr_method", self.ocr_method_combo.currentData())
        self.settings.setValue("ocr_languages", self._selected_tesseract_languages())
        self.settings.setValue("ollama_url", self.ollama_url_edit.text().strip())
        self.settings.setValue("ollama_model", self._current_ollama_model_tag())
        self.settings.setValue("mode", self.mode_combo.currentData())
        self.settings.setValue("field_mode", self.field_method_combo.currentData())
        self.settings.setValue("selection_method", self.selection_method_combo.currentData())
        self.settings.setValue("selector_text", self.selector_edit.text().strip())
        self.settings.setValue("html_snippet", self.html_edit.toPlainText())
        self.settings.setValue("start_page", self.start_page_spin.value())
        self.settings.setValue("end_page", self.end_page_spin.value())
        self.settings.remove("current_page")
        self.settings.setValue("wait_seconds", self.wait_spin.value())
        self.settings.setValue("reuse_area", self.reuse_area_checkbox.isChecked())
        self.settings.setValue("reselect_area", self.reselect_area_checkbox.isChecked())
        self.settings.setValue("restore_area_on_startup", self.restore_area_on_startup_checkbox.isChecked())
        self.settings.setValue("overlay_enabled", self.area_overlay.isVisible())
        self.settings.setValue("locator", json.dumps(self.current_locator.to_dict()) if self.current_locator else "")
        self.settings.setValue("area", json.dumps(self.capture_area.to_dict()) if self.capture_area else "")
        self.settings.setValue("main_splitter_state", self.main_splitter.saveState())
        self.settings.setValue("right_splitter_state", self.right_splitter.saveState())

    def _field_mode_changed(self, *_args) -> None:
        mode = self.field_method_combo.currentData()
        self.selector_edit.setEnabled(mode in {"preset", "css"})
        self.selector_edit.setReadOnly(mode == "preset")
        self.html_edit.setEnabled(mode == "html")
        self.apply_field_button.setEnabled(mode in {"preset", "css", "html"})
        self.use_focused_button.setEnabled(mode == "active")
        self.start_picker_button.setEnabled(mode == "picked")
        self.use_picked_button.setEnabled(mode == "picked")
        if mode == "preset":
            self.selector_edit.setText(ELIBRO_PAGE_FIELD_SELECTOR)
        self._refresh_guidance()

    def _update_mode_state(self, *_args) -> None:
        mode = self.mode_combo.currentData()
        automatic = mode == "automatic"
        self.reselect_area_checkbox.setEnabled(not automatic)
        self.capture_next_button.setEnabled(mode in {"semi", "manual"})
        self.next_page_button.setEnabled(mode in {"semi", "manual"})
        self.stop_button.setEnabled(self.auto_running)
        if automatic:
            self.reselect_area_checkbox.setChecked(False)
        self._refresh_guidance()

    def _update_ocr_state(self, *_args) -> None:
        method = self.ocr_method_combo.currentData()
        use_tesseract = method == "tesseract"
        use_ollama = method == "ollama"
        use_surya = method == "surya"
        controls_enabled = not self._ocr_export_running
        self._update_ocr_action_texts()
        self.ocr_method_combo.setEnabled(controls_enabled)
        self.ocr_language_combo.setEnabled(use_tesseract and controls_enabled)
        self.ocr_language_hint.setEnabled(use_tesseract and controls_enabled)
        self.tesseract_language_state_label.setEnabled(use_tesseract and controls_enabled)
        self.install_tesseract_language_button.setEnabled(use_tesseract and controls_enabled)
        self.ollama_url_edit.setEnabled(use_ollama and controls_enabled)
        self.ollama_model_combo.setEnabled(use_ollama and controls_enabled)
        self.pull_ollama_model_button.setEnabled(use_ollama and controls_enabled)
        self.test_ollama_button.setEnabled(use_ollama and controls_enabled)
        self.install_surya_button.setEnabled(use_surya and controls_enabled)
        self.export_pdf_ocr_button.setEnabled(not self._ocr_export_running)
        self.export_ocr_document_button.setEnabled((use_ollama or use_surya) and controls_enabled)
        self.ollama_model_hint_value.setEnabled(use_ollama and controls_enabled)
        self._update_ollama_model_hint()
        self._refresh_tesseract_language_state()

    def _update_ollama_model_hint(self, *_args) -> None:
        current_model = self._current_ollama_model_tag()
        preset = next((entry for entry in OLLAMA_MODEL_PRESETS if entry.model == current_model), None)
        if preset is not None:
            self.ollama_model_hint_value.setText(preset.summary)
            return
        if current_model:
            self.ollama_model_hint_value.setText(self._tr("ollama_model_custom_hint", model=current_model))
            return
        self.ollama_model_hint_value.setText("")

    def _refresh_guidance(self) -> None:
        if self.auto_running:
            self.operation_value.setText(self._tr("operation_running_auto"))
            self.instruction_value.setText(self._tr("instruction_auto"))
            return
        if self._ocr_export_running:
            self.operation_value.setText(self._ocr_export_title or self._tr("export_pdf_ocr"))
            self.instruction_value.setText(self._ocr_last_status or self._tr("message_ocr_starting"))
            return

        mode = self.mode_combo.currentData()
        field_mode = self.field_method_combo.currentData()
        self.operation_value.setText(
            self._tr("operation_browser_opened") if self.browser.is_open else self._tr("operation_ready")
        )

        if self.area_picker_pending:
            self.instruction_value.setText(self._tr("instruction_area_picker"))
        elif self.picker_pending:
            self.instruction_value.setText(self._tr("instruction_picker"))
        elif field_mode == "active" and self.current_locator is None:
            self.instruction_value.setText(self._tr("instruction_focus_field"))
        elif not self.browser.is_open:
            self.instruction_value.setText(self._tr("instruction_ready"))
        elif self.capture_area is None:
            self.instruction_value.setText(self._tr("instruction_select_area"))
        elif mode == "manual":
            self.instruction_value.setText(self._tr("instruction_manual"))
        elif mode == "semi":
            self.instruction_value.setText(self._tr("instruction_semi"))
        else:
            self.instruction_value.setText(self._tr("instruction_auto"))

        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(self._tr("progress_idle"))

    def _update_area_label(self) -> None:
        if self.capture_area is None:
            self.area_value_label.setText(self._tr("area_not_selected"))
        else:
            self.area_value_label.setText(
                f"{self.capture_area.width} x {self.capture_area.height} @ "
                f"({self.capture_area.x}, {self.capture_area.y})"
            )
        self._update_toggle_overlay_text()

    def _update_toggle_overlay_text(self) -> None:
        self.toggle_overlay_button.setText(
            self._tr("hide_overlay") if self.area_overlay.isVisible() else self._tr("show_overlay")
        )

    def _show_area_overlays(self, area: CaptureArea) -> None:
        self.area_overlay.show_area(area)
        self.area_resizer.show_area(area)
        self._update_toggle_overlay_text()

    def _hide_area_overlays(self) -> None:
        self.area_overlay.hide_overlay()
        self.area_resizer.hide_overlay()
        self._update_toggle_overlay_text()

    def _clear_area_overlays(self) -> None:
        self.area_overlay.clear_area()
        self.area_resizer.clear_area()
        self._update_toggle_overlay_text()

    def _area_changed_from_overlay(self, area: CaptureArea) -> None:
        self.capture_area = area
        self.area_overlay.show_area(area)
        self._update_area_label()

    def _update_field_summary(self) -> None:
        if self.current_locator is None:
            self.field_summary_value.setText(self._tr("status_locator_none"))
            return
        description = self.current_locator.description or self.current_locator.source or self.current_locator.strategy
        self.field_summary_value.setText(
            self._tr("status_field_saved", description=description, value=self.current_locator.value)
        )

    def _extract_command_from_message(self, message: str) -> str | None:
        marker = "Suggested command:"
        if marker in message:
            command = message.split(marker, 1)[1].strip()
            return command or None
        quoted_parts = message.split("`")
        for index in range(1, len(quoted_parts), 2):
            command = quoted_parts[index].strip()
            if command:
                return command
        return None

    def _show_error(self, message: str) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle(self._tr("error_title"))
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)

        copy_command = self._extract_command_from_message(message)
        copy_button = None
        if copy_command:
            copy_button = dialog.addButton(self._tr("copy_command"), QMessageBox.ButtonRole.ActionRole)

        dialog.exec()
        if copy_button is not None and dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(copy_command)
            self._log(self._tr("message_command_copied"))

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, self._tr("warning_title"), message)

    def _start_busy_indicator(self, operation_text: str, instruction_text: str) -> None:
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("")
        self.operation_value.setText(operation_text)
        self.instruction_value.setText(instruction_text)
        self.open_browser_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _finish_busy_indicator(self) -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        self.open_browser_button.setEnabled(True)
        self._refresh_guidance()
        QApplication.processEvents()

    def _create_progress_dialog(
        self,
        title: str,
        label_text: str,
        maximum: int = 0,
        modality: Qt.WindowModality = Qt.WindowModality.ApplicationModal,
    ) -> QProgressDialog:
        dialog = QProgressDialog(label_text, "", 0, maximum, self)
        dialog.setWindowTitle(title)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setWindowModality(modality)
        dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        dialog.show()
        dialog.raise_()
        QApplication.processEvents()
        return dialog

    def _show_closing_progress(self, message: str) -> None:
        dialog = QProgressDialog(message, "", 0, 0, self)
        dialog.setWindowTitle(self._tr("closing_title"))
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        dialog.show()
        dialog.raise_()
        self._closing_dialog = dialog
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _update_closing_progress(self, message: str) -> None:
        if self._closing_dialog is None:
            return
        self._closing_dialog.setLabelText(message)
        QApplication.processEvents()

    def _close_closing_progress(self) -> None:
        if self._closing_dialog is not None:
            self._closing_dialog.close()
            self._closing_dialog = None
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")

    def _browse_output_dir(self) -> None:
        current = self.output_dir_edit.text().strip() or str(self._default_output_dir())
        selected = QFileDialog.getExistingDirectory(self, self._tr("output_dir"), current)
        if selected:
            self.output_dir_edit.setText(selected)

    def _open_output_dir(self) -> None:
        path = ensure_directory(Path(self.output_dir_edit.text().strip() or self._default_output_dir()))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _import_pdf_pages(self) -> None:
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("import_pdf"),
            str(self._default_export_dir()),
            "PDF (*.pdf)",
        )
        if not source_path:
            return

        pdf_path = Path(source_path)
        output_dir = self._ensure_output_dir()
        temp_root = self._capture_temp_dir(output_dir)
        import_dir = ensure_directory(temp_root / f"import_{sanitize_filename(pdf_path.stem)}_{int(time.time())}")
        dialog = self._create_progress_dialog(self._tr("import_pdf"), self._tr("message_importing_pdf"))
        dialog.setRange(0, 0)

        try:
            def update_progress(current: int, total: int, status: str) -> None:
                dialog.setLabelText(status or self._tr("message_importing_pdf"))
                if total > 0:
                    dialog.setRange(0, total)
                    dialog.setValue(max(0, min(current, total)))
                else:
                    dialog.setRange(0, 0)
                QApplication.processEvents()

            rendered_paths = render_pdf_to_images(
                pdf_path,
                import_dir,
                base_name=pdf_path.stem,
                progress_callback=update_progress,
            )
            for page_number, image_path in enumerate(rendered_paths, start=1):
                self._append_capture_record(image_path, page_number=page_number)

            imported_base_name = sanitize_filename(pdf_path.stem) or self._default_base_name()
            self.base_name_edit.setText(imported_base_name)

            self._log(self._tr("message_pdf_imported", pages=len(rendered_paths), path=str(pdf_path)))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            dialog.close()
            QApplication.processEvents()

    def _open_browser(self) -> None:
        url = self.url_edit.text().strip()
        use_profile = self.use_profile_checkbox.isChecked()
        user_data_dir = self.user_data_dir_edit.text().strip()
        profile_directory = self.profile_directory_edit.text().strip() or "Default"
        if use_profile and not user_data_dir:
            self._show_error(self._tr("error_profile_dir_required"))
            return
        self._start_busy_indicator(self._tr("operation_opening_browser"), self._tr("instruction_opening_browser"))
        error_message: str | None = None
        try:
            self.browser.launch(
                url=url,
                browser_name=self.browser_type_combo.currentData(),
                use_profile=use_profile,
                user_data_dir=user_data_dir or None,
                profile_directory=profile_directory,
            )
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
        finally:
            self._finish_busy_indicator()

        if error_message is not None:
            self._show_error(error_message)
            return

        if not self.base_name_edit.text().strip() or self.base_name_edit.text().strip() == "capture":
            self.base_name_edit.setText(self.browser.suggest_base_name(url))

        if use_profile:
            self._log(self._tr("message_browser_opened_profile"))
            self._log(self._tr("note_profile_lock"))
        else:
            self._log(self._tr("message_browser_opened", url=url or "about:blank"))
        self._refresh_guidance()

    def _close_browser(self) -> None:
        self._stop_area_picker_polling()
        self.browser.close()
        self.picker_pending = False
        self.area_picker_pending = False
        self._log(self._tr("message_browser_closed"))
        self._refresh_guidance()

    def _resolve_locator_from_inputs(self) -> ElementLocator:
        mode = self.field_method_combo.currentData()
        if mode == "preset":
            return ElementLocator("css", ELIBRO_PAGE_FIELD_SELECTOR, self._tr("field_method_preset"), "preset")
        if mode == "css":
            selector = self.selector_edit.text().strip()
            if not selector:
                raise ValueError(self._tr("error_empty_selector"))
            return ElementLocator("css", selector, self._tr("field_method_css"), "css")
        if mode == "html":
            snippet = self.html_edit.toPlainText().strip()
            if not snippet:
                raise ValueError(self._tr("error_empty_snippet"))
            locator = locator_from_html_snippet(snippet)
            if locator is None:
                raise ValueError(self._tr("error_invalid_snippet"))
            locator.source = "html"
            return locator
        if self.current_locator is None:
            raise ValueError(self._tr("error_field_required"))
        return self.current_locator

    def _set_locator(self, locator: ElementLocator) -> None:
        self.current_locator = locator
        self._update_field_summary()

    def _apply_field(self) -> None:
        try:
            locator = self._resolve_locator_from_inputs()
            self._set_locator(locator)
            self.picker_pending = False
            self._log(self._tr("message_field_saved", value=locator.value))
            if self.browser.is_open and self.browser.validate_locator(locator):
                self._log(self._tr("message_field_validated"))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            self._refresh_guidance()

    def _use_focused_element(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        try:
            locator = self.browser.get_active_element_locator()
            if locator is None:
                raise RuntimeError(self._tr("error_field_required"))
            self._set_combo_by_data(self.field_method_combo, "active")
            self._set_locator(locator)
            self._log(self._tr("message_field_saved", value=locator.value))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            self._refresh_guidance()

    def _start_picker(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        try:
            self._stop_area_picker_polling()
            self.browser.begin_picker()
            self.picker_pending = True
            self.area_picker_pending = False
            self._log(self._tr("message_picker_started"))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            self._refresh_guidance()

    def _use_picked_element(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        try:
            locator = self.browser.get_picked_locator()
            if locator is None:
                raise RuntimeError(self._tr("error_no_pick"))
            self._set_combo_by_data(self.field_method_combo, "picked")
            self._set_locator(locator)
            self.picker_pending = False
            self.area_picker_pending = False
            self._log(self._tr("message_field_saved", value=locator.value))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            self._refresh_guidance()

    def _start_area_picker(self) -> bool:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return False
        try:
            self._stop_area_picker_polling()
            self.capture_area = None
            self._clear_area_overlays()
            self._update_area_label()
            self.browser.begin_picker()
            self.area_picker_pending = True
            self.picker_pending = False
            self.area_picker_timer.start()
            self._log(self._tr("message_area_picker_started"))
            return True
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return False
        finally:
            self._refresh_guidance()

    def _stop_area_picker_polling(self) -> None:
        if self.area_picker_timer.isActive():
            self.area_picker_timer.stop()
        self.area_picker_pending = False

    def _apply_picked_area(self, area: CaptureArea) -> None:
        self.capture_area = area
        self._stop_area_picker_polling()
        self._show_area_overlays(self.capture_area)
        self._update_area_label()
        self._log(
            self._tr(
                "message_area_selected",
                width=self.capture_area.width,
                height=self.capture_area.height,
                x=self.capture_area.x,
                y=self.capture_area.y,
            )
        )
        self._refresh_guidance()

    def _poll_picked_area(self) -> None:
        if not self.area_picker_pending:
            self._stop_area_picker_polling()
            return
        if not self.browser.is_open:
            self._stop_area_picker_polling()
            self._refresh_guidance()
            return
        try:
            area = self.browser.get_picked_capture_area()
        except Exception as exc:  # noqa: BLE001
            self._stop_area_picker_polling()
            self._show_error(str(exc))
            self._refresh_guidance()
            return
        if area is None:
            return
        self._apply_picked_area(area)

    def _read_current_page(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        if self.current_locator is None:
            self._show_error(self._tr("error_field_required"))
            return
        try:
            value = self.browser.read_page_value(self.current_locator)
            digits = "".join(character for character in value if character.isdigit())
            if digits:
                self.current_page_spin.setValue(int(digits))
            self._log(self._tr("message_page_read", page=value or digits or "?"))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _go_to_current_page(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        if self.current_locator is None:
            self._show_error(self._tr("error_field_required"))
            return
        page = self.current_page_spin.value()
        try:
            self.browser.go_to_page(
                self.current_locator,
                page,
                0.0,
            )
            self._log(self._tr("message_page_changed", page=page))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _start_area_selection(self) -> bool:
        mode = self.selection_method_combo.currentData()
        if mode == "browser":
            return self._start_area_picker()
        return self._select_area(mode)

    def _select_area(self, mode: str) -> bool:
        self._stop_area_picker_polling()
        self.operation_value.setText(self._tr("operation_selecting_area"))
        previous_area = self.capture_area
        previous_overlay_visible = self.area_overlay.isVisible()
        self._clear_area_overlays()
        selector = AreaSelectorDialog(self.translator, mode, self)
        if selector.exec() != QDialog.DialogCode.Accepted or selector.selected_area is None:
            if previous_area is not None and previous_overlay_visible:
                self._show_area_overlays(previous_area)
            self._refresh_guidance()
            return False
        self.capture_area = selector.selected_area
        self._show_area_overlays(self.capture_area)
        self._update_area_label()
        self._log(
            self._tr(
                "message_area_selected",
                width=self.capture_area.width,
                height=self.capture_area.height,
                x=self.capture_area.x,
                y=self.capture_area.y,
            )
        )
        self._refresh_guidance()
        return True

    def _toggle_overlay(self) -> None:
        if self.capture_area is None:
            self._show_warning(self._tr("error_area_required"))
            return
        if self.area_overlay.isVisible():
            self._hide_area_overlays()
        else:
            self._show_area_overlays(self.capture_area)

    def _clear_area(self) -> None:
        self._stop_area_picker_polling()
        self.capture_area = None
        self._clear_area_overlays()
        self._update_area_label()
        self._refresh_guidance()

    def _start_over_session(self) -> None:
        if self.auto_running or self._ocr_export_running:
            self._show_warning(self._tr("warning_start_over_busy"))
            return

        if not self.capture_records and not self.log_view.toPlainText().strip():
            self._refresh_guidance()
            return

        answer = QMessageBox.question(
            self,
            self._tr("warning_title"),
            self._tr("confirm_start_over"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.stop_requested = False
        self.picker_pending = False
        self._stop_area_picker_polling()
        self.browser.stop_picker()
        self.gallery_list.clear()
        self.capture_records.clear()
        self.capture_counter = 0
        self.current_preview_path = None
        self.preview_label.clear()
        self._cleanup_temp_directories()
        self.log_view.clear()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(self._tr("progress_idle"))
        self._refresh_guidance()

    def _ensure_output_dir(self) -> Path:
        path_text = self.output_dir_edit.text().strip()
        path = Path(path_text) if path_text else self._default_output_dir()
        self.output_dir_edit.setText(str(path))
        return ensure_directory(path)

    def _capture_temp_dir(self, output_dir: Path | None = None) -> Path:
        base_dir = output_dir or self._ensure_output_dir()
        temp_dir = ensure_directory(base_dir / "temp")
        self.session_temp_dirs.add(temp_dir)
        return temp_dir

    def _capture_path_from_item(self, item: QListWidgetItem) -> Path:
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _find_capture_record(self, path: Path) -> CaptureRecord | None:
        for record in self.capture_records:
            if record.file_path == path:
                return record
        return None

    def _gallery_icon(self, path: Path) -> QIcon:
        pixmap = QPixmap(str(path))
        return QIcon(pixmap) if not pixmap.isNull() else QIcon(str(path))

    def _page_number_for_capture(self) -> int | None:
        if self.browser.is_open and self.current_locator is not None:
            return self.current_page_spin.value()
        return None

    def _make_capture_path(self, page_number: int | None) -> Path:
        output_dir = self._ensure_output_dir()
        temp_dir = self._capture_temp_dir(output_dir)
        base_name = self._ensure_base_name()
        label = f"_p{page_number:04d}" if page_number is not None else ""
        while True:
            self.capture_counter += 1
            candidate = temp_dir / f"{base_name}_{self.capture_counter:04d}{label}.png"
            if not candidate.exists():
                return candidate

    def _append_capture_record(
        self,
        file_path: Path,
        page_number: int | None = None,
        *,
        record_index: int | None = None,
        label: str | None = None,
        log_message: str | None = None,
    ) -> CaptureRecord:
        if record_index is None:
            self.capture_counter += 1
            record_index = self.capture_counter
        else:
            self.capture_counter = max(self.capture_counter, record_index)

        record = CaptureRecord(
            index=record_index,
            file_path=file_path,
            label=label or file_path.name,
            page_number=page_number,
        )
        self.capture_records.append(record)

        item = QListWidgetItem(self._gallery_icon(file_path), record.label)
        item.setData(Qt.ItemDataRole.UserRole, str(file_path))
        self.gallery_list.addItem(item)
        self.gallery_list.setCurrentItem(item)
        if log_message:
            self._log(log_message)
        self._refresh_guidance()
        return record

    def _capture_to_file(self, output_path: Path) -> Path:
        if self.capture_area is None:
            raise RuntimeError(self._tr("error_area_required"))

        area_rect = QRect(self.capture_area.x, self.capture_area.y, self.capture_area.width, self.capture_area.height)
        should_hide_main_window = self.isVisible() and self.frameGeometry().intersects(area_rect)
        overlay_was_visible = self.area_overlay.isVisible()

        try:
            if overlay_was_visible:
                self._hide_area_overlays()
            if should_hide_main_window:
                self.hide()
            self._wait_with_events(0.18)
            return capture_screen_area(self.capture_area, output_path)
        finally:
            if should_hide_main_window:
                self.showNormal()
            if overlay_was_visible and self.capture_area is not None:
                self._show_area_overlays(self.capture_area)
                self.area_overlay.set_countdown_text("")

    def _capture_single(self, page_number: int | None, apply_wait: bool = False) -> CaptureRecord | None:
        output_path = self._make_capture_path(page_number)
        self.operation_value.setText(self._tr("operation_capturing"))
        if apply_wait:
            self._run_wait_countdown(self.wait_spin.value())
        self._capture_to_file(output_path)
        return self._append_capture_record(
            output_path,
            page_number=page_number,
            record_index=self.capture_counter,
            log_message=self._tr("message_capture_saved", path=str(output_path)),
        )

    def _after_capture_flow(self) -> None:
        if self.reselect_area_checkbox.isChecked():
            self._log(self._tr("message_area_cleared_reselect"))
            self._clear_area()
        elif not self.reuse_area_checkbox.isChecked():
            self._log(self._tr("message_area_cleared_no_reuse"))
            self._clear_area()

    def _run_wait_countdown(self, seconds: float) -> None:
        total_seconds = max(float(seconds), 0.0)
        if total_seconds <= 0:
            return

        overlay_was_visible = self.area_overlay.isVisible()
        if self.capture_area is not None and not overlay_was_visible:
            self.area_overlay.show_area(self.capture_area)

        deadline = time.perf_counter() + total_seconds
        while True:
            remaining = max(0.0, deadline - time.perf_counter())
            if self.capture_area is not None:
                self.area_overlay.set_countdown_text(f"{remaining:.1f}s")
            QApplication.processEvents()
            if remaining <= 0.0:
                break
            time.sleep(0.05)

        if self.capture_area is not None:
            self.area_overlay.set_countdown_text("")
            if not overlay_was_visible:
                self.area_overlay.hide_overlay()

    def _capture_now(self) -> None:
        try:
            if self.capture_area is None:
                self._show_warning(self._tr("error_select_area_first"))
                return
            page_number = self._page_number_for_capture()
            record = self._capture_single(page_number, apply_wait=False)
            if record:
                self._after_capture_flow()
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _capture_and_next(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        if self.current_locator is None:
            self._show_error(self._tr("error_field_required"))
            return
        try:
            if self.capture_area is None:
                self._show_warning(self._tr("error_select_area_first"))
                return
            current_page = self.current_page_spin.value()
            record = self._capture_single(current_page)
            if record is None:
                return
            next_page = current_page + 1
            self.current_page_spin.setValue(next_page)
            self.browser.go_to_page(
                self.current_locator,
                next_page,
                0.0,
            )
            self._log(self._tr("message_page_changed", page=next_page))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _next_page_only(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        if self.current_locator is None:
            self._show_error(self._tr("error_field_required"))
            return
        next_page = self.current_page_spin.value() + 1
        self.current_page_spin.setValue(next_page)
        try:
            self.browser.go_to_page(
                self.current_locator,
                next_page,
                0.0,
            )
            self._log(self._tr("message_page_changed", page=next_page))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _run_automatic_range(self) -> None:
        if not self.browser.is_open:
            self._show_error(self._tr("error_browser_required"))
            return
        if self.current_locator is None:
            self._show_error(self._tr("error_field_required"))
            return
        if self.capture_area is None:
            self._show_warning(self._tr("error_select_area_first"))
            return

        start_page = self.start_page_spin.value()
        end_page = self.end_page_spin.value()
        if end_page < start_page:
            self._show_warning(self._tr("error_invalid_range"))
            return

        self.auto_running = True
        self.stop_requested = False
        self._update_mode_state()
        total = end_page - start_page + 1
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"0 / {total}")
        self._refresh_guidance()
        QApplication.processEvents()

        try:
            for index, page_number in enumerate(range(start_page, end_page + 1), start=1):
                if self.stop_requested:
                    break
                self.current_page_spin.setValue(page_number)
                self.browser.go_to_page(
                    self.current_locator,
                    page_number,
                    self.wait_spin.value(),
                    wait_callback=self._run_wait_countdown,
                )
                self._log(self._tr("message_page_changed", page=page_number))
                self._capture_single(page_number)
                self.progress_bar.setValue(index)
                self.progress_bar.setFormat(f"{index} / {total}")
                QApplication.processEvents()
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            stopped = self.stop_requested
            self.auto_running = False
            self._update_mode_state()
            self._refresh_guidance()
            self._log(self._tr("message_auto_stopped" if stopped else "message_auto_done"))

    def _stop_run(self) -> None:
        self.stop_requested = True

    def _sync_capture_records_from_gallery(self, *_args) -> None:
        record_map = {str(record.file_path): record for record in self.capture_records}
        ordered_records: list[CaptureRecord] = []
        for row in range(self.gallery_list.count()):
            item = self.gallery_list.item(row)
            record = record_map.get(str(item.data(Qt.ItemDataRole.UserRole)))
            if record is not None:
                ordered_records.append(record)
        self.capture_records = ordered_records

    def _show_gallery_context_menu(self, position) -> None:
        item = self.gallery_list.itemAt(position)
        if item is None:
            return

        self.gallery_list.setCurrentItem(item)
        menu = QMenu(self)
        retake_action = menu.addAction(self._tr("retake_capture"))
        delete_action = menu.addAction(self._tr("delete_capture"))
        chosen_action = menu.exec(self.gallery_list.viewport().mapToGlobal(position))
        if chosen_action is retake_action:
            self._retake_capture_item(item)
        elif chosen_action is delete_action:
            self._delete_capture_item(item)

    def _open_capture_in_viewer(self, item: QListWidgetItem) -> None:
        path = self._capture_path_from_item(item)
        if not path.exists():
            self._show_warning(self._tr("error_capture_missing", path=str(path)))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._show_warning(self._tr("error_open_capture_viewer"))

    def _retake_capture_item(self, item: QListWidgetItem) -> None:
        path = self._capture_path_from_item(item)
        record = self._find_capture_record(path)
        if record is None:
            self._show_warning(self._tr("error_no_selection"))
            return
        if self.capture_area is None:
            self._show_warning(self._tr("error_select_area_first"))
            return

        try:
            if record.page_number is not None:
                if not self.browser.is_open:
                    raise RuntimeError(self._tr("error_browser_required"))
                if self.current_locator is None:
                    raise RuntimeError(self._tr("error_field_required"))
                self.current_page_spin.setValue(record.page_number)
                self.browser.go_to_page(
                    self.current_locator,
                    record.page_number,
                    0.0,
                )
                self._log(self._tr("message_page_changed", page=record.page_number))

            self.operation_value.setText(self._tr("operation_capturing"))
            self._capture_to_file(record.file_path)
            item.setIcon(self._gallery_icon(record.file_path))
            item.setText(record.file_path.name)
            self.gallery_list.setCurrentItem(item)
            self.current_preview_path = record.file_path
            self._refresh_preview()
            self._log(self._tr("message_capture_retaken", path=str(record.file_path)))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            self._refresh_guidance()

    def _delete_capture_item(self, item: QListWidgetItem) -> None:
        path = self._capture_path_from_item(item)
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                self._show_error(str(exc))
                return

        row = self.gallery_list.row(item)
        self.gallery_list.takeItem(row)
        self.capture_records = [record for record in self.capture_records if record.file_path != path]
        if self.gallery_list.count() > 0 and self.gallery_list.currentItem() is None:
            self.gallery_list.setCurrentRow(min(row, self.gallery_list.count() - 1))
        self._log(self._tr("message_deleted", path=str(path)))
        self._update_preview_from_selection()

    def _update_preview_from_selection(self, *_args) -> None:
        item = self.gallery_list.currentItem()
        if item is None:
            self.current_preview_path = None
            self.preview_label.clear()
            return
        self.current_preview_path = Path(item.data(Qt.ItemDataRole.UserRole))
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self.current_preview_path is None or not self.current_preview_path.exists():
            self.preview_label.clear()
            return
        pixmap = QPixmap(str(self.current_preview_path))
        if pixmap.isNull():
            self.preview_label.clear()
            return
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _looks_like_ollama_connection_error(self, message: str) -> bool:
        lowered = message.lower()
        markers = (
            "could not connect",
            "connection refused",
            "failed to establish",
            "winerror 10061",
            "timed out",
            "name or service not known",
            "nodename nor servname provided",
        )
        return any(marker in lowered for marker in markers)

    def _show_ollama_error(self, message: str, base_url: str) -> None:
        if self._looks_like_ollama_connection_error(message):
            self._show_error(
                self._tr(
                    "error_ollama_unreachable",
                    url=base_url,
                    install_command=ollama_install_command(),
                    run_command=ollama_run_command(),
                )
            )
            return
        self._show_error(message)

    def _ollama_pull_progress_text(self, status: str, completed: int, total: int) -> str:
        if total > 0:
            return self._tr(
                "message_ollama_pull_progress",
                status=status,
                completed=_format_bytes(completed),
                total=_format_bytes(total),
            )
        return status or self._tr("message_ollama_connecting")

    def _pull_selected_ollama_model(self) -> None:
        base_url = self._ollama_url()
        try:
            model_name = self._selected_ollama_model()
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return

        dialog = self._create_progress_dialog(
            self._tr("pull_ollama_model"),
            self._tr("message_ollama_connecting"),
            self._OLLAMA_PROGRESS_SCALE,
        )
        try:
            def update_progress(completed: int, total: int, status: str) -> None:
                dialog.setLabelText(self._ollama_pull_progress_text(status, completed, total))
                if total > 0:
                    if dialog.maximum() != self._OLLAMA_PROGRESS_SCALE:
                        dialog.setRange(0, self._OLLAMA_PROGRESS_SCALE)
                    scaled_value = int(round((max(0, min(completed, total)) / total) * self._OLLAMA_PROGRESS_SCALE))
                    dialog.setValue(max(0, min(scaled_value, self._OLLAMA_PROGRESS_SCALE)))
                else:
                    dialog.setRange(0, 0)
                QApplication.processEvents()

            pull_ollama_model(base_url, model_name, progress_callback=update_progress)
            self._log(self._tr("message_ollama_model_pulled", model=model_name))
        except Exception as exc:  # noqa: BLE001
            self._show_ollama_error(str(exc), base_url)
        finally:
            dialog.close()
            QApplication.processEvents()

    def _test_selected_ollama_model(self) -> None:
        base_url = self._ollama_url()
        try:
            model_name = self._selected_ollama_model()
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return

        dialog = self._create_progress_dialog(self._tr("test_ollama"), self._tr("message_ollama_testing"))
        try:
            version = test_ollama_connection(base_url, model_name)
            installed = ollama_model_installed(base_url, model_name)
            self._log(
                self._tr(
                    "message_ollama_test_ok",
                    url=base_url,
                    version=version,
                    model=model_name,
                    installed=self._tr("yes") if installed else self._tr("no"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._show_ollama_error(str(exc), base_url)
        finally:
            dialog.close()
            QApplication.processEvents()

    def _install_surya(self) -> None:
        if is_frozen():
            self._show_error(
                self._tr("error_surya_install_failed", command='pip install --upgrade surya-ocr "transformers>=4.56.1,<5"')
            )
            return
        dialog = self._create_progress_dialog(self._tr("install_surya"), self._tr("message_surya_installing"))
        dialog.setRange(0, 0)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "surya-ocr",
                    "transformers>=4.56.1,<5",
                ],
                capture_output=True,
                text=False,
                check=False,
                cwd=str(Path.cwd()),
            )
            if completed.returncode != 0:
                error_text = completed.stderr.decode("utf-8", errors="replace").strip() or completed.stdout.decode(
                    "utf-8", errors="replace"
                ).strip()
                raise RuntimeError(error_text or self._tr("error_surya_install_failed", command=surya_install_command()))
            if not surya_available():
                raise RuntimeError(self._tr("error_surya_install_failed", command=surya_install_command()))
            self._log(self._tr("message_surya_installed"))
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
        finally:
            dialog.close()
            QApplication.processEvents()

    def _run_tesseract_language_install(self, language_codes: list[str]) -> list[Path]:
        if not language_codes:
            build_tesseract_runtime_tessdata()
            self._refresh_tesseract_installed_languages()
            self._refresh_tesseract_language_state()
            self._log(self._tr("message_tesseract_runtime_repaired"))
            return []

        dialog = self._create_progress_dialog(
            self._tr("install_tesseract_languages"),
            self._tr("message_tesseract_install_starting"),
            len(language_codes),
        )
        try:
            def update_progress(completed: int, total: int, status: str) -> None:
                dialog.setLabelText(status or self._tr("message_tesseract_install_starting"))
                total_value = max(total, 1)
                dialog.setRange(0, total_value)
                dialog.setValue(max(0, min(completed, total_value)))
                QApplication.processEvents()

            installed_paths = install_tesseract_languages(language_codes, progress_callback=update_progress)
        finally:
            dialog.close()
            QApplication.processEvents()

        self._refresh_tesseract_installed_languages()
        self._refresh_tesseract_language_state()
        if installed_paths:
            self._log(self._tr("message_tesseract_languages_installed", languages=", ".join(language_codes)))
        return installed_paths

    def _install_selected_tesseract_languages(self) -> None:
        if find_tesseract_executable() is None:
            self._show_error(
                f"Tesseract OCR is not installed or not in PATH. Install it and try again. Suggested command: {tesseract_install_command()}"
            )
            return

        selected_value, requested_codes, _effective_value, missing_languages = self._analyze_tesseract_languages()
        try:
            if missing_languages:
                self._run_tesseract_language_install(missing_languages)
            else:
                self._run_tesseract_language_install([])
                self._log(
                    self._tr(
                        "message_tesseract_languages_already_installed",
                        languages=selected_value or "+".join(requested_codes),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _prepare_tesseract_languages_for_export(self) -> str:
        if find_tesseract_executable() is None:
            raise RuntimeError(
                f"Tesseract OCR is not installed or not in PATH. Install it and try again. Suggested command: {tesseract_install_command()}"
            )
        selected_value, _requested_codes, effective_value, missing_languages = self._analyze_tesseract_languages()
        if not missing_languages:
            return selected_value or "eng"

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setWindowTitle(self._tr("warning_title"))
        prompt.setText(
            self._tr(
                "prompt_tesseract_install_missing",
                requested=selected_value,
                missing=", ".join(missing_languages),
                effective=effective_value,
            )
        )
        install_button = prompt.addButton(self._tr("install_now"), QMessageBox.ButtonRole.AcceptRole)
        use_available_button = None
        if effective_value and effective_value != selected_value:
            use_available_button = prompt.addButton(self._tr("use_available_languages"), QMessageBox.ButtonRole.ActionRole)
        cancel_button = prompt.addButton(self._tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        prompt.exec()

        clicked_button = prompt.clickedButton()
        if clicked_button is install_button:
            self._run_tesseract_language_install(missing_languages)
            refreshed_selected, _requested_codes, _effective_value, refreshed_missing = self._analyze_tesseract_languages()
            if refreshed_missing:
                raise RuntimeError(
                    self._tr(
                        "error_tesseract_languages_missing",
                        requested=refreshed_selected,
                        missing=", ".join(refreshed_missing),
                    )
                )
            return refreshed_selected or "eng"
        if use_available_button is not None and clicked_button is use_available_button:
            self._log(
                self._tr(
                    "message_tesseract_language_fallback",
                    requested=selected_value,
                    effective=effective_value,
                    available=", ".join(self.installed_tesseract_languages) or self._tr("none"),
                )
            )
            return effective_value or "eng"
        if clicked_button is cancel_button:
            raise RuntimeError(self._tr("error_ocr_cancelled"))
        raise RuntimeError(self._tr("error_ocr_cancelled"))

    def _set_ocr_export_busy(self, busy: bool) -> None:
        self._ocr_export_running = busy
        self.controls_scroll.setEnabled(not busy)
        self.gallery_list.setEnabled(not busy)
        self._update_mode_state()
        self._update_ocr_state()
        if not busy:
            self._refresh_guidance()

    def _start_ocr_export(
        self,
        target_path: Path,
        *,
        output_kind: str = "pdf",
        output_format: str = "pdf",
        title_key: str = "export_pdf_ocr",
        success_key: str = "message_export_pdf_ocr",
    ) -> None:
        if self._ocr_export_running:
            raise RuntimeError(self._tr("warning_ocr_export_running"))

        capture_paths = self._selected_capture_paths()
        if not capture_paths:
            raise RuntimeError(self._tr("error_no_captures"))

        method = str(self.ocr_method_combo.currentData() or "").strip()
        languages = ""
        base_url = DEFAULT_OLLAMA_URL
        model_name = ""
        if method == "tesseract":
            if output_kind != "pdf":
                raise RuntimeError(self._tr("error_ocr_text_requires_ollama"))
            languages = self._prepare_tesseract_languages_for_export()
        elif method == "ollama":
            base_url = self._ollama_url()
            model_name = self._selected_ollama_model()
        elif method == "surya":
            if not surya_available():
                raise RuntimeError(self._tr("error_surya_not_installed", command=surya_install_command()))
        else:
            raise RuntimeError(self._tr("error_ocr_method_required"))

        total = len(capture_paths)
        self._ocr_last_status = ""
        self._ocr_export_title = self._tr(title_key)
        self._ocr_export_success_key = success_key
        self._ocr_export_dialog = self._create_progress_dialog(
            self._ocr_export_title,
            self._tr("message_ocr_starting"),
            total,
            modality=Qt.WindowModality.NonModal,
        )
        self._ocr_export_dialog.setRange(0, 0)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(f"0 / {total}")
        self.operation_value.setText(self._ocr_export_title)
        self.instruction_value.setText(self._tr("message_ocr_starting"))
        self._set_ocr_export_busy(True)
        self._log(self._tr("message_ocr_starting"))

        thread = QThread(self)
        worker = OcrExportWorker(
            capture_paths=capture_paths,
            target_path=target_path,
            method=method,
            output_kind=output_kind,
            output_format=output_format,
            languages=languages,
            base_url=base_url,
            model_name=model_name,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_ocr_export_progress)
        worker.finished.connect(self._on_ocr_export_finished)
        worker.error.connect(self._on_ocr_export_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_ocr_export_thread_finished)

        self._ocr_export_thread = thread
        self._ocr_export_worker = worker
        thread.start()

    def _on_ocr_export_thread_finished(self) -> None:
        self._ocr_export_thread = None
        self._ocr_export_worker = None

    def _is_ocr_progress_busy_step(self, completed: int, total: int, status: str) -> bool:
        total_value = max(total, 1)
        lowered = status.strip().lower()
        if completed >= total_value:
            return False
        if lowered.startswith("added ocr page"):
            return False
        if lowered.startswith("collected ocr page"):
            return False
        if lowered.endswith("ocr complete") or lowered.endswith("complete"):
            return False
        return True

    def _on_ocr_export_progress(self, completed: int, total: int, status: str) -> None:
        total_value = max(total, 1)
        current_value = max(0, min(completed, total_value))
        label_text = status or self._tr("message_ocr_starting")
        busy_step = self._is_ocr_progress_busy_step(current_value, total_value, label_text)

        if self._ocr_export_dialog is not None:
            self._ocr_export_dialog.setLabelText(label_text)
            if busy_step:
                self._ocr_export_dialog.setRange(0, 0)
            else:
                self._ocr_export_dialog.setRange(0, total_value)
                self._ocr_export_dialog.setValue(current_value)

        self.operation_value.setText(self._ocr_export_title or self._tr("export_pdf_ocr"))
        self.instruction_value.setText(label_text)
        if busy_step:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, total_value)
            self.progress_bar.setValue(current_value)
        self.progress_bar.setFormat(f"{current_value} / {total_value}")

        if label_text != self._ocr_last_status:
            self._ocr_last_status = label_text
            self._log(label_text)

    def _finish_ocr_export_ui(self) -> None:
        if self._ocr_export_dialog is not None:
            self._ocr_export_dialog.close()
            self._ocr_export_dialog = None
        self._ocr_last_status = ""
        self._ocr_export_title = ""
        self._ocr_export_success_key = "message_export_pdf_ocr"
        self._set_ocr_export_busy(False)

    def _on_ocr_export_finished(self, path: str) -> None:
        success_key = self._ocr_export_success_key
        self._finish_ocr_export_ui()
        self._remember_export_dir(path)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.progress_bar.setFormat(self._tr("progress_idle"))
        self._log(self._tr(success_key, path=path))
        self._open_export_folder_for_path(path)

    def _on_ocr_export_error(self, message: str) -> None:
        self._finish_ocr_export_ui()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(self._tr("progress_idle"))
        if self.ocr_method_combo.currentData() == "ollama":
            self._show_ollama_error(message, self._ollama_url())
        else:
            self._show_error(message)

    def _export_pdf_with_optional_ocr(self, target_path: Path, use_ocr: bool) -> Path:
        capture_paths = self._selected_capture_paths()
        if not capture_paths:
            raise RuntimeError(self._tr("error_no_captures"))
        if use_ocr:
            raise RuntimeError("OCR export now runs through the background worker.")
        return export_pdf(capture_paths, target_path)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_preview()

    def _export_zip(self) -> None:
        if not self.capture_records:
            self._show_warning(self._tr("error_no_captures"))
            return
        output_dir = ensure_directory(self._default_export_dir())
        default_name = self._export_file_stem("ZIP") + ".zip"
        target, _ = QFileDialog.getSaveFileName(self, self._tr("export_zip"), str(output_dir / default_name), "ZIP (*.zip)")
        if not target:
            return
        path = export_zip((record.file_path for record in self.capture_records), Path(target))
        self._remember_export_dir(target)
        self._log(self._tr("message_export_zip", path=str(path)))
        self._open_export_folder_for_path(path)

    def _export_pdf(self) -> None:
        if not self.capture_records:
            self._show_warning(self._tr("error_no_captures"))
            return
        output_dir = ensure_directory(self._default_export_dir())
        default_name = self._export_file_stem("PDF") + ".pdf"
        target, _ = QFileDialog.getSaveFileName(self, self._tr("export_pdf"), str(output_dir / default_name), "PDF (*.pdf)")
        if not target:
            return
        try:
            path = self._export_pdf_with_optional_ocr(Path(target), use_ocr=False)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._remember_export_dir(target)
        self._log(self._tr("message_export_pdf", path=str(path)))
        self._open_export_folder_for_path(path)

    def _export_pdf_with_ocr(self) -> None:
        if not self.capture_records:
            self._show_warning(self._tr("error_no_captures"))
            return
        output_dir = ensure_directory(self._default_export_dir())
        title_key = self._ocr_pdf_title_key()
        success_key = self._ocr_pdf_success_key()
        target, _ = QFileDialog.getSaveFileName(
            self,
            self._tr(title_key),
            str(output_dir / self._ocr_pdf_default_name()),
            "PDF (*.pdf)",
        )
        if not target:
            return
        try:
            self._start_ocr_export(
                Path(target),
                output_kind="pdf",
                output_format="pdf",
                title_key=title_key,
                success_key=success_key,
            )
        except Exception as exc:  # noqa: BLE001
            if self.ocr_method_combo.currentData() == "ollama":
                self._show_ollama_error(str(exc), self._ollama_url())
            else:
                self._show_error(str(exc))
            return

    def _export_ocr_document(self) -> None:
        if not self.capture_records:
            self._show_warning(self._tr("error_no_captures"))
            return
        if self.ocr_method_combo.currentData() not in {"ollama", "surya"}:
            self._show_warning(self._tr("error_ocr_text_requires_ollama"))
            return

        output_dir = ensure_directory(self._default_export_dir())
        filters = "Text (*.txt);;Markdown (*.md);;HTML (*.html);;EPUB (*.epub);;DOCX (*.docx)"
        target, selected_filter = QFileDialog.getSaveFileName(
            self,
            self._tr("export_ocr_document"),
            str(output_dir / self._ocr_text_default_name("txt")),
            filters,
        )
        if not target:
            return

        output_format = self._ocr_text_export_extension(target, selected_filter)
        target_path = Path(target)
        if target_path.suffix.strip() == "":
            target_path = target_path.with_suffix(f".{output_format}")
        try:
            self._start_ocr_export(
                target_path,
                output_kind="text",
                output_format=output_format,
                title_key="export_surya_ocr_document" if self.ocr_method_combo.currentData() == "surya" else "export_ocr_document",
                success_key="message_export_surya_ocr_document" if self.ocr_method_combo.currentData() == "surya" else "message_export_ocr_document",
            )
        except Exception as exc:  # noqa: BLE001
            if self.ocr_method_combo.currentData() == "ollama":
                self._show_ollama_error(str(exc), self._ollama_url())
            else:
                self._show_error(str(exc))
            return

    def _delete_selected(self) -> None:
        item = self.gallery_list.currentItem()
        if item is None:
            self._show_warning(self._tr("error_no_selection"))
            return
        self._delete_capture_item(item)

    def _wait_with_events(self, seconds: float) -> None:
        deadline = time.perf_counter() + max(seconds, 0.0)
        while time.perf_counter() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)

    def _cleanup_temp_directories(self) -> None:
        for temp_dir in list(self.session_temp_dirs):
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        self.session_temp_dirs.clear()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._closing_in_progress:
            event.accept()
            return
        if self._ocr_export_running:
            self._show_warning(self._tr("warning_ocr_export_running"))
            event.ignore()
            return

        self._closing_in_progress = True
        self._show_closing_progress(self._tr("closing_message"))
        try:
            self._update_closing_progress(self._tr("closing_saving_settings"))
            self._save_settings()

            self._update_closing_progress(self._tr("closing_hiding_overlay"))
            self._stop_area_picker_polling()
            self._hide_area_overlays()

            self._update_closing_progress(self._tr("closing_browser"))
            try:
                self.browser.close()
            except Exception:  # noqa: BLE001
                pass

            self._update_closing_progress(self._tr("closing_cleanup"))
            self._clear_area_overlays()
            self._cleanup_temp_directories()
        finally:
            self._close_closing_progress()
            self._closing_in_progress = False
        super().closeEvent(event)


def run_app() -> int:
    app = QApplication(sys.argv)
    icon_path = app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()
    return app.exec()
