from __future__ import annotations

import importlib.util
import re
import zipfile
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QGuiApplication

from .models import CaptureArea


INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')
RenderProgressCallback = Callable[[int, int, str], None]


def sanitize_filename(value: str, fallback: str = "capture") -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", value).strip().strip(".")
    return cleaned or fallback


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def capture_screen_area(area: CaptureArea, output_path: Path) -> Path:
    if not area.is_valid():
        raise ValueError("Capture area is not valid.")

    ensure_directory(output_path.parent)
    screen = QGuiApplication.screenAt(QPoint(*area.center)) or QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("No screen is available for capture.")

    screen_geometry = screen.geometry()
    if not screen_geometry.contains(QPoint(*area.center)):
        raise RuntimeError("The selected area is outside the available screen geometry.")

    local_x = area.x - screen_geometry.x()
    local_y = area.y - screen_geometry.y()
    pixmap = screen.grabWindow(0, local_x, local_y, area.width, area.height)
    if pixmap.isNull():
        raise RuntimeError("The screenshot capture returned an empty image.")
    if not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Unable to save screenshot to {output_path}.")
    return output_path


def export_zip(image_paths: Iterable[Path], zip_path: Path) -> Path:
    ensure_directory(zip_path.parent)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image_path in image_paths:
            archive.write(image_path, arcname=image_path.name)
    return zip_path


def export_pdf(image_paths: Iterable[Path], pdf_path: Path) -> Path:
    ordered_paths = list(image_paths)
    if not ordered_paths:
        raise ValueError("No screenshots are available to export.")

    ensure_directory(pdf_path.parent)
    images = [Image.open(path).convert("RGB") for path in ordered_paths]
    try:
        images[0].save(
            pdf_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=images[1:],
        )
    finally:
        for image in images:
            image.close()
    return pdf_path


def render_pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    base_name: str | None = None,
    scale: float = 2.0,
    progress_callback: RenderProgressCallback | None = None,
) -> list[Path]:
    if importlib.util.find_spec("pypdfium2") is None:
        raise RuntimeError(
            "PDF import requires pypdfium2. Install it with: .\\venv\\Scripts\\python.exe -m pip install pypdfium2"
        )

    import pypdfium2 as pdfium

    ensure_directory(output_dir)
    document = pdfium.PdfDocument(str(pdf_path))
    rendered_paths: list[Path] = []
    safe_base_name = sanitize_filename(base_name or pdf_path.stem or "imported_pdf")

    try:
        total_pages = len(document)
        for page_index in range(total_pages):
            if progress_callback is not None:
                progress_callback(page_index, total_pages, f"Rendering PDF page {page_index + 1}/{total_pages}")

            page = document[page_index]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                output_path = output_dir / f"{safe_base_name}_pdf_{page_index + 1:04d}.png"
                image.save(output_path, format="PNG")
                rendered_paths.append(output_path)
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()

            if progress_callback is not None:
                progress_callback(page_index + 1, total_pages, f"Rendered PDF page {page_index + 1}/{total_pages}")
    finally:
        document.close()

    if not rendered_paths:
        raise RuntimeError("The selected PDF did not produce any pages.")
    return rendered_paths
