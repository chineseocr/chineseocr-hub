"""Bounded input decoding; only generated PNG previews are sent to models."""

import shutil
import threading
import warnings
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from .store import uid

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 20
Image.MAX_IMAGE_PIXELS = 16_000_000
PDF_LOCK = threading.Lock()  # PDFium is not thread-safe, even across different documents.


def ingest(store, name, stream):
    """Validate a bounded upload and persist its original plus normalized page previews.

    Reads the stream in chunks, validates actual content, serializes PDFium access and removes partial files on failure. Returned pages match the PNGs sent to models."""
    key = uid()
    folder = store.directory / "files" / key
    folder.mkdir(parents=True, mode=0o700)
    try:
        size = 0
        original = folder / "original"
        with original.open("wb") as out:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("单文件上限为 20MB")
                out.write(chunk)
        if not size:
            raise ValueError("文件为空")
        header = original.open("rb")
        with header:
            is_pdf = header.read(5) == b"%PDF-"
        pages = []

        def save_page(image, index, source_size=None, source_unit="pixels"):
            """Normalize orientation/transparency, bound dimensions and retain actual page dimensions for provenance."""
            image = ImageOps.exif_transpose(image)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                canvas = Image.new("RGBA", rgba.size, "white")
                image = Image.alpha_composite(canvas, rgba)
            image = image.convert("RGB")
            source_width, source_height = source_size or image.size
            image.thumbnail((1600, 1600))
            image.save(folder / f"page-{index}.png", "PNG")
            pages.append(
                {
                    "page": index,
                    "width": image.width,
                    "height": image.height,
                    "source_width": source_width,
                    "source_height": source_height,
                    "source_unit": source_unit,
                }
            )

        if is_pdf:
            with PDF_LOCK, closing(pdfium.PdfDocument(str(original))) as pdf:
                if not 1 <= len(pdf) <= MAX_PAGES:
                    raise ValueError("PDF 支持 1–20 页；请先拆分较大的文件")
                for index in range(len(pdf)):
                    with closing(pdf[index]) as page:
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            raise ValueError("PDF 页面尺寸无效")
                        bitmap = page.render(scale=min(2, 1600 / max(width, height)))
                        try:
                            save_page(bitmap.to_pil(), index + 1, (width, height), "pdf_points")
                        finally:
                            bitmap.close()
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(original) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP", "TIFF"}:
                        raise ValueError("仅支持 PNG、JPEG、WEBP、单页 TIFF 和 PDF")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError("多帧图片请转换为 PDF 后上传")
                    image.load()
                    save_page(image, 1)
        body = {
            "storage_key": key,
            "name": Path(name or "document").name[:200],
            "pages": pages,
            "size": size,
            "stored_bytes": sum(path.stat().st_size for path in folder.iterdir()),
            "media_type": "application/pdf" if is_pdf else "application/octet-stream",
        }
        file_id = store.put("files", body)
        return dict(id=file_id, **body)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def preview_path(store, file, page):
    # storage_key comes exclusively from ingest(), never from a client-supplied path.
    """Resolve a private preview from server-generated metadata, never from client path fragments."""
    return store.directory / "files" / file["storage_key"] / f"page-{page}.png"
