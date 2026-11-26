"""Turn the files a session left behind into images: for the model (``view_image``, ``render_page``), for the page
and for the judge.

HTML and SVG are rendered by a Chromium browser already on the computer, in headless screenshot mode: Microsoft Edge
(present on every Windows 10 and 11), else Chrome or Chromium; ``VIXL_SANDBOX_BROWSER`` points at another. PDFs use
pypdfium2, PowerPoint and Word files go through LibreOffice when it is installed, and a ``.vixl`` document is rendered
by the Vixl version that made it.
"""

import base64
from functools import lru_cache
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from PIL import Image

RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
KINDS = {**{ext: "image" for ext in RASTER}, ".svg": "svg", ".html": "html", ".htm": "html", ".pdf": "pdf",
         ".pptx": "pptx", ".docx": "docx", ".xlsx": "xlsx", ".vixl": "vixl", ".mp4": "video", ".webm": "video",
         ".md": "text", ".txt": "text", ".csv": "text", ".json": "text", ".css": "text", ".py": "text"}
MODEL_SIDE = 1024  # longest side of an image sent to the model
PREVIEW_SIDE = 1568  # longest side of the stored preview (and of what the judge sees)
MAX_VIEWPORT = 4000
_BROWSER_SLOTS = threading.Semaphore(2)
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

WINDOWS_BROWSERS = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
MAC_BROWSERS = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
LINUX_BROWSERS = ["microsoft-edge", "google-chrome", "chromium", "chromium-browser"]


class PreviewError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def browser():
    """A Chromium-based browser executable, or None."""
    if os.environ.get("VIXL_SANDBOX_BROWSER"):
        return os.environ["VIXL_SANDBOX_BROWSER"]
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            for relative in WINDOWS_BROWSERS:
                if base and Path(base, relative).is_file():
                    return str(Path(base, relative))
        return None
    if sys.platform == "darwin":
        return next((path for path in MAC_BROWSERS if Path(path).is_file()), None)
    return next((shutil.which(name) for name in LINUX_BROWSERS if shutil.which(name)), None)


def render_html_available():
    return browser() is not None


def kind(path):
    return KINDS.get(Path(path).suffix.lower(), "other")


def hidden(relative):
    return any(part.startswith(".") or part == "__pycache__" for part in Path(relative).parts)


def deliverables(workspace):
    """Every file the session left in its workspace (hidden files and caches left out), newest first."""
    workspace = Path(workspace)
    found = []
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if path.is_file() and not path.is_symlink() and not hidden(relative):
            stat = path.stat()
            found.append({"path": relative.as_posix(), "size": stat.st_size, "kind": kind(path),
                          "mtime": stat.st_mtime})
    return sorted(found, key=lambda item: -item["mtime"])


def fit(image, side):
    image = image.convert("RGBA") if image.mode in ("P", "LA", "PA") else image
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    if max(image.size) > side:
        image = image.copy()
        image.thumbnail((side, side), Image.Resampling.LANCZOS)
    return image


def png_bytes(image, side):
    buffer = io.BytesIO()
    fit(image, side).save(buffer, format="PNG")
    return buffer.getvalue()


def image_block(png):
    return {"type": "image", "media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}


def svg_size(path):
    """The SVG's own width and height in px (from width/height or the viewBox), or None."""
    try:
        head = Path(path).read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    tag = re.search(r"<svg\b[^>]*>", head, re.S | re.I)
    if not tag:
        return None

    def number(name):
        found = re.search(rf'\b{name}\s*=\s*["\']\s*([\d.]+)\s*(px)?\s*["\']', tag.group(0))
        return float(found.group(1)) if found else None

    width, height = number("width"), number("height")
    if not (width and height):
        box = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', tag.group(0))
        if box:
            width, height = float(box.group(1)), float(box.group(2))
    if not (width and height):
        return None
    return max(1, min(int(round(width)), MAX_VIEWPORT)), max(1, min(int(round(height)), MAX_VIEWPORT))


def screenshot(source, width=None, height=None):
    """PNG bytes of an HTML or SVG file rendered by the headless browser."""
    executable = browser()
    if executable is None:
        raise PreviewError("rendering HTML and SVG needs Microsoft Edge, Google Chrome or Chromium")
    source = Path(source).resolve()
    if not (width and height) and source.suffix.lower() == ".svg":
        width, height = svg_size(source) or (width, height)
    width, height = int(width or 1280), int(height or 800)
    with _BROWSER_SLOTS, tempfile.TemporaryDirectory(prefix="vixl-sandbox-shot-") as folder:
        out = Path(folder) / "shot.png"
        command = [executable, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", f"--user-data-dir={Path(folder) / 'profile'}",
                   f"--window-size={width},{height}", "--force-device-scale-factor=1",
                   "--default-background-color=00000000", f"--screenshot={out}", source.as_uri()]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            command.insert(1, "--no-sandbox")
        try:
            subprocess.run(command, capture_output=True, timeout=60, **NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreviewError(f"the browser could not render {source.name}: {exc}") from None
        if not out.is_file():
            raise PreviewError(f"the browser could not render {source.name}")
        return out.read_bytes()


def pdf_page(path, page=0):
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    try:
        index = max(0, min(int(page), len(document) - 1))
        return document[index].render(scale=2).to_pil()
    finally:
        document.close()


def office_to_pdf(path, folder):
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None and os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            candidate = Path(base or "", "LibreOffice", "program", "soffice.exe")
            if candidate.is_file():
                office = str(candidate)
    if office is None:
        return None
    subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", str(folder), str(path)],
                   capture_output=True, timeout=120, **NO_WINDOW)
    result = Path(folder) / (Path(path).stem + ".pdf")
    return result if result.is_file() else None


VIXL_RENDER = "import sys; from vixl import Project; Project.load(sys.argv[1]).render().save(sys.argv[2])"


def vixl_render(path, python):
    """Render a .vixl document with the Vixl version that made it (``python`` is that version's interpreter)."""
    if not python:
        raise PreviewError("no Vixl version is known for this .vixl document")
    with tempfile.TemporaryDirectory(prefix="vixl-sandbox-render-") as folder:
        out = Path(folder) / "render.png"
        done = subprocess.run([str(python), "-c", VIXL_RENDER, str(path), str(out)], capture_output=True, text=True,
                              timeout=180, **NO_WINDOW)
        if not out.is_file():
            raise PreviewError(f"Vixl could not render {Path(path).name}: {done.stderr.strip()[-300:]}")
        with Image.open(out) as image:
            return image.copy()


def load_image(path, page=0, vixl_python=None):
    """A PIL image of any previewable file."""
    path = Path(path)
    file_kind = kind(path)
    if file_kind == "image":
        with Image.open(path) as image:
            image.seek(0)
            return image.copy()
    if file_kind in ("svg", "html"):
        return Image.open(io.BytesIO(screenshot(path)))
    if file_kind == "pdf":
        return pdf_page(path, page)
    if file_kind in ("pptx", "docx", "xlsx"):
        with tempfile.TemporaryDirectory(prefix="vixl-sandbox-office-") as folder:
            pdf = office_to_pdf(path, folder)
            if pdf is None:
                raise PreviewError(f"previewing {path.suffix} files needs LibreOffice")
            return pdf_page(pdf, page)
    if file_kind == "vixl":
        return vixl_render(path, vixl_python)
    raise PreviewError(f"cannot show a {path.suffix or 'file without an extension'} as an image")


ORDER = ["image", "svg", "pdf", "html", "pptx", "docx", "xlsx", "vixl"]


def make_preview(workspace, out, prefer=(), vixl_python=None):
    """Write the main deliverable in ``workspace`` as ``out`` (PNG); return (source path, note) or (None, reason).
    Files in ``prefer`` (the ones a round changed) come first, then by kind, then the newest."""
    files = [item for item in deliverables(workspace) if item["kind"] in ORDER]
    files.sort(key=lambda item: (item["path"] not in prefer, ORDER.index(item["kind"])))
    reasons = []
    for item in files:
        try:
            image = load_image(Path(workspace) / item["path"], vixl_python=vixl_python)
        except Exception as exc:  # noqa: BLE001 - try the next file; report why none worked
            reasons.append(f"{item['path']}: {exc}")
            continue
        Path(out).write_bytes(png_bytes(image, PREVIEW_SIDE))
        note = "only the editable .vixl document was left; nothing was exported" if item["kind"] == "vixl" else ""
        return item["path"], note
    return None, "; ".join(reasons) or "no previewable file was left in the workspace"
