from __future__ import annotations

import io
import os
import re
import math
import uuid
import shlex
import subprocess
import shutil as _shutil
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
from shutil import which as sh_which

from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, ImageFilter
from flask import (
    Flask, jsonify, render_template, request, redirect, url_for,
    send_from_directory, send_file, flash, Blueprint, current_app, 
)


# Optional dependencies 
try:
    import yt_dlp  
except Exception:
    yt_dlp = None
    
try:
    import pikepdf
    _HAS_PIKEPDF = True
except Exception:
    _HAS_PIKEPDF = False

try:
    from PyPDF2 import PdfReader, PdfWriter
    _HAS_PYPDF2 = True
except Exception:
    _HAS_PYPDF2 = False

try:
    from rembg import remove as rembg_remove  # pip install rembg
    _HAS_REMBG = True
except Exception:
    _HAS_REMBG = False


# Make imageio-ffmpeg's bundled ffmpeg visible (works on Railway too)
def has_ffmpeg() -> bool:
    ff = which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg as _iioff
            ff = _iioff.get_ffmpeg_exe()
        except Exception:
            return False
    try:
        return subprocess.run([ff, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
    except Exception:
        return False


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = "dev-secret-key"  # replace for production


# -------------------------
# Storage layout
# -------------------------
BASE = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE / "uploads"
DOWNLOAD_DIR = BASE / "downloads"

UPLOADS_BY_TOOL = {
    "pdf_decrypter": UPLOAD_DIR / "pdf_decrypter",
    "pdf_encrypter": UPLOAD_DIR / "pdf_encrypter",
    "pdf_combiner": UPLOAD_DIR / "pdf_combiner",  
    "yt_vid_downloader": UPLOAD_DIR / "yt_vid_downloader",
    "video_cropper": UPLOAD_DIR / "video_cropper",
    "audio_to_text": UPLOAD_DIR / "audio_to_text",  
    "image_combiner": UPLOAD_DIR / "image_combiner",
    "image_sketch": UPLOAD_DIR / "image_sketch",  
    "image_background_remover": UPLOAD_DIR / "image_background_remover",  
}

DOWNLOADS_BY_TOOL = {
    "pdf_decrypter": DOWNLOAD_DIR / "pdf_decrypter",
    "pdf_encrypter": DOWNLOAD_DIR / "pdf_encrypter",
    "pdf_combiner": DOWNLOAD_DIR / "pdf_combiner",  
    "yt_vid_downloader": DOWNLOAD_DIR / "yt_vid_downloader",
    "video_cropper": DOWNLOAD_DIR / "video_cropper", 
    "audio_to_text": DOWNLOAD_DIR / "audio_to_text", 
    "image_combiner": DOWNLOAD_DIR / "image_combiner",
    "image_sketch": DOWNLOAD_DIR / "image_sketch",   
    "image_background_remover": DOWNLOAD_DIR / "image_background_remover",  
}

# Ensure folders exist
for p in [UPLOAD_DIR, DOWNLOAD_DIR, *UPLOADS_BY_TOOL.values(), *DOWNLOADS_BY_TOOL.values()]:
    p.mkdir(parents=True, exist_ok=True)

ALLOWED_PDF_EXT = {".pdf"}
ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_FILES = 10
ALLOWED_MEDIA_EXTS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".amr",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"
}


# -------------------------
# Utilities
# -------------------------
def list_files(dirpath: Path) -> List[str]:
    """Newest-first file names in the directory."""
    if not dirpath.exists():
        return []
    files = [p for p in dirpath.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files]


def ensure_ext(filename: str, desired_ext: str) -> str:
    base, ext = os.path.splitext(filename)
    if not base:
        base = "file"
    if ext.lower() != desired_ext.lower():
        return f"{base}{desired_ext}"
    return filename


def combine_images(image_paths: List[Path], orientation: str = "vertical", target: Optional[int] = None) -> Image.Image:
    """
    Open images, optionally resize to a target width/height, and stack vertically or horizontally.

    orientation: "vertical" or "horizontal"
    target: if vertical, interpreted as target_width; if horizontal, interpreted as target_height
    """
    if not image_paths:
        raise ValueError("No images provided")

    imgs = [Image.open(p).convert("RGB") for p in image_paths]

    if orientation not in {"vertical", "horizontal"}:
        orientation = "vertical"

    if orientation == "vertical":
        # Determine target width
        target_width = target if target is not None else max(img.width for img in imgs)

        # Resize to same width (preserve aspect)
        resized = []
        for img in imgs:
            if img.width != target_width:
                new_h = int(img.height * (target_width / img.width))
                resized.append(img.resize((target_width, new_h), Image.LANCZOS))
            else:
                resized.append(img)

        total_height = sum(img.height for img in resized)
        combined = Image.new("RGB", (target_width, total_height), color=(255, 255, 255))

        y = 0
        for img in resized:
            combined.paste(img, (0, y))
            y += img.height

    else:  # horizontal
        # Determine target height
        target_height = target if target is not None else max(img.height for img in imgs)

        # Resize to same height (preserve aspect)
        resized = []
        for img in imgs:
            if img.height != target_height:
                new_w = int(img.width * (target_height / img.height))
                resized.append(img.resize((new_w, target_height), Image.LANCZOS))
            else:
                resized.append(img)

        total_width = sum(img.width for img in resized)
        combined = Image.new("RGB", (total_width, target_height), color=(255, 255, 255))

        x = 0
        for img in resized:
            combined.paste(img, (x, 0))
            x += img.width

    return combined


# -------------------------
# Landing 
# -------------------------
@app.route("/")
def landing():
    return render_template("landing.html")


# -------------------------
# Image Combiner
# -------------------------
@app.route("/image_combiner", methods=["GET", "POST"])
def image_combiner():
    if request.method == "GET":
        return render_template("image_combiner.html", max_files=MAX_FILES)

    try:
        # How many inputs?
        try:
            num = int(request.form.get("num_images", "2"))
        except Exception:
            num = 2
        num = max(2, min(MAX_FILES, num))

        # Collect files in the given order
        files = []
        for i in range(1, num + 1):
            f = request.files.get(f"image_{i}")
            if f and f.filename:
                files.append(f)

        if len(files) < 2:
            flash("Please upload at least two images.", "warning")
            return render_template("image_combiner.html", max_files=MAX_FILES)

        # Orientation (vertical/horizontal)
        orientation = (request.form.get("orientation") or "vertical").lower().strip()
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"

        # Save uploads
        up_dir = UPLOADS_BY_TOOL["image_combiner"]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_paths: List[Path] = []
        for idx, f in enumerate(files, start=1):
            name = secure_filename(f.filename)
            base, ext = os.path.splitext(name)
            if ext.lower() not in ALLOWED_IMG_EXTS:
                flash(f"Unsupported file type: {name}", "danger")
                return render_template("image_combiner.html", max_files=MAX_FILES)
            safe_name = f"{stamp}_{idx}_{base}{ext.lower()}"
            dst = up_dir / safe_name
            f.save(dst)
            saved_paths.append(dst)

        # Combine (auto target dimension is picked inside)
        combined = combine_images(saved_paths, orientation=orientation)

        # Output file name
        raw_name = (request.form.get("output_name") or "").strip() or f"combined_{stamp}.png"
        safe_out = secure_filename(raw_name)
        safe_out = ensure_ext(safe_out, ".png")

        out_dir = DOWNLOADS_BY_TOOL["image_combiner"]
        out_path = out_dir / safe_out
        counter = 1
        base, ext = os.path.splitext(safe_out)
        while out_path.exists():
            out_path = out_dir / f"{base}_{counter}{ext}"
            counter += 1

        combined.save(out_path, format="PNG", quality=95)

        # Success message will show when they visit Downloads, but here we also auto-download.
        # The file is ALREADY saved in your downloads page folder.
        return send_file(
            out_path,
            mimetype="image/png",
            as_attachment=True,
            download_name=out_path.name
        )

    except Exception as e:
        flash(f"Combine failed: {e}", "danger")
        return render_template("image_combiner.html", max_files=MAX_FILES)


# -------------------------
# YouTube Video Downloader
# -------------------------
def _has_video_stream(path: Path) -> bool:
    """Return True if ffprobe reports at least one video stream in the file."""
    if sh_which("ffprobe") is None:
        # If ffprobe isn't present, assume OK to avoid blocking downloads.
        return True
    try:
        # Ask ffprobe to list video stream indexes; if any, we’re good.
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=index",
            "-of", "csv=p=0", str(path)
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return bool(out.stdout.strip())
    except Exception:
        return True


@app.route("/yt_vid_downloader", methods=["GET", "POST"])
def yt_vid_downloader():
    """Download a YouTube URL as MP4 (video) or MP3 (audio-only) and auto-send to browser."""
    if request.method == "GET":
        return render_template("yt_vid_downloader.html")

    # --- POST begins ---
    if yt_dlp is None:
        flash("Missing dependency: yt-dlp. Run: pip install yt-dlp", "danger")
        return render_template("yt_vid_downloader.html")

    if sh_which("ffmpeg") is None:
        flash("Missing dependency: ffmpeg. Install it (e.g., brew install ffmpeg) and restart.", "danger")
        return render_template("yt_vid_downloader.html")

    video_url = (request.form.get("video_url") or "").strip()
    output_name = (request.form.get("output_name") or "").strip()
    fmt = (request.form.get("format") or "mp4").lower().strip()

    if not video_url:
        flash("Please paste a YouTube URL.", "warning")
        return render_template("yt_vid_downloader.html")

    # Normalize base filename
    safe = secure_filename(output_name) if output_name else "video"
    base, _ext = os.path.splitext(safe)
    final_base = base or "video"

    out_dir: Path = DOWNLOADS_BY_TOOL["yt_vid_downloader"]
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / f"{final_base}.%(ext)s")

    # ---------- AUDIO ONLY (MP3) ----------
    if fmt == "mp3":
        ydl_opts = {
            "outtmpl": outtmpl,
            "quiet": True,
            "noprogress": True,
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
            ],
        }
        expected = out_dir / f"{final_base}.mp3"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except Exception as e:
            flash(f"Download failed: {e}", "danger")
            return render_template("yt_vid_downloader.html")

        path = expected if expected.exists() else max(
            out_dir.glob(f"{final_base}.*"), key=lambda p: p.stat().st_mtime, default=None
        )
        if not path or not path.exists():
            flash("We couldn't locate the downloaded file. Please try again.", "danger")
            return render_template("yt_vid_downloader.html")

        return send_file(path, as_attachment=True, download_name=path.name)

    # ---------- VIDEO (MP4) ----------
    # 1) Prefer a progressive H.264/AAC MP4 (already has both tracks).
    # 2) Else H.264 video + AAC audio (separate) merged to MP4.
    # 3) Else any MP4; else best available.
    primary_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "format": (
            "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[acodec^=mp4a]/"
            "best[ext=mp4]/"
            "best"
        ),
        "merge_output_format": "mp4",
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},  # remux (no re-encode) when possible
        ],
    }
    expected_mp4 = out_dir / f"{final_base}.mp4"

    def newest_match() -> Path | None:
        return max(out_dir.glob(f"{final_base}.*"), key=lambda p: p.stat().st_mtime, default=None)

    # Pass 1: try to get a QuickTime-friendly MP4 without re-encoding
    try:
        with yt_dlp.YoutubeDL(primary_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        flash(f"Download failed: {e}", "danger")
        return render_template("yt_vid_downloader.html")

    saved = expected_mp4 if expected_mp4.exists() else newest_match()
    if not saved or not saved.exists():
        flash("We couldn't locate the downloaded file. Please try again.", "danger")
        return render_template("yt_vid_downloader.html")

    # If it isn't an MP4 (e.g., WEBM) or it somehow lacks a video stream, do a guaranteed fallback.
    need_fallback = (saved.suffix.lower() != ".mp4") or (not _has_video_stream(saved))
    if need_fallback:
        # Re-encode to H.264/AAC MP4 so QuickTime plays it, and ensure both video+audio present.
        fallback_opts = {
            "outtmpl": outtmpl,
            "quiet": True,
            "noprogress": True,
            "format": "bestvideo+bestaudio/best",
            "recode_video": "mp4",
            "postprocessor_args": [
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-profile:v", "high",
                "-level", "4.1",
                "-movflags", "+faststart",
                "-c:a", "aac",
                "-b:a", "192k",
            ],
        }
        try:
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                ydl.download([video_url])
        except Exception as e:
            flash(f"Re-encode fallback failed: {e}", "danger")
            return render_template("yt_vid_downloader.html")

        saved = expected_mp4 if expected_mp4.exists() else newest_match()
        if not saved or not saved.exists():
            flash("We couldn't locate the re-encoded file.", "danger")
            return render_template("yt_vid_downloader.html")

    # Normalize filename to <final_base>.mp4
    target = expected_mp4
    if saved != target:
        try:
            saved.rename(target)
        except Exception:
            target = saved

    # Final guard: if still somehow no video stream, inform user.
    if not _has_video_stream(target):
        flash(
            "We produced an MP4 but it appears to be audio-only. "
            "Try a different video quality or URL.",
            "danger",
        )
        # Still send the file so the user has *something*:
        return send_file(target, as_attachment=True, download_name=target.name)

    # Success — send immediately and keep a copy in downloads/youtube
    return send_file(target, as_attachment=True, download_name=target.name)


# configure max upload size (optional)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB limit; adjust as needed


# Allowed extension helper
def allowed_file(filename):
    return '.' in filename and filename.lower().rsplit('.', 1)[1] == 'pdf'


def ensure_dirs(*dirs: Path):
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def tool_dirs(tool: str):
    """Return (uploads_subdir, downloads_subdir) for a tool key."""
    return (UPLOAD_DIR / tool, DOWNLOAD_DIR / tool)


def ts_name(name: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}__{name}"


# -------------------------
# PDF Decrypter
# -------------------------
@app.route("/pdf_decrypter", methods=["GET", "POST"])
def pdf_decrypter():
    TOOL = "pdf_decrypter"
    up_dir, down_dir = tool_dirs(TOOL)
    ensure_dirs(up_dir, down_dir)

    if request.method == "GET":
        return render_template("pdf_decrypter.html")

    uploaded = request.files.get("pdf_file")
    password = request.form.get("password", "")
    out_name = (request.form.get("output_name") or "").strip()

    if not uploaded or uploaded.filename == "":
        return render_template("pdf_decrypter.html", error="No file uploaded.")
    if "." not in uploaded.filename or uploaded.filename.lower().rsplit(".", 1)[1] != "pdf":
        return render_template("pdf_decrypter.html", error="Please upload a PDF file.")

    orig_name = secure_filename(uploaded.filename)
    # save encrypted file into uploads/pdf_decrypter/
    uploaded_name = ts_name(orig_name)
    upload_path = up_dir / uploaded_name
    uploaded.save(upload_path)

    # decide output filename (clean name for download; timestamped for disk)
    if not out_name:
        out_name = f"decrypted_{orig_name}"
    if not out_name.lower().endswith(".pdf"):
        out_name += ".pdf"
    saved_out_name = ts_name(out_name)
    out_path = down_dir / saved_out_name

    # decrypt -> write to downloads/pdf_decrypter/
    try:
        import pikepdf
        try:
            with pikepdf.open(str(upload_path), password=password) as pdf:
                pdf.save(str(out_path))
        except pikepdf._qpdf.PasswordError:
            return render_template("pdf_decrypter.html", error="Incorrect password for this PDF.")
        except Exception:
            # fall back to PyPDF2
            raise
    except Exception:
        try:
            from PyPDF2 import PdfReader, PdfWriter
            with open(upload_path, "rb") as f:
                reader = PdfReader(f)
                if reader.is_encrypted:
                    res = reader.decrypt(password)
                    if res in (0, False):
                        return render_template("pdf_decrypter.html", error="Incorrect password for this PDF.")
                writer = PdfWriter()
                for p in reader.pages:
                    writer.add_page(p)
                with open(out_path, "wb") as g:
                    writer.write(g)
        except Exception:
            current_app.logger.exception("PDF decryption failed")
            return render_template("pdf_decrypter.html", error="Failed to decrypt the PDF.")

    # stream the decrypted file; disk copy already exists in downloads/pdf_decrypter/
    return send_file(
        out_path,
        as_attachment=True,
        download_name=out_name,
        mimetype="application/pdf",
        max_age=0,
    )


# -------------------------
# PDF Encrypter
# -------------------------
@app.route("/pdf_encrypter", methods=["GET", "POST"])
def pdf_encrypter():
    """
    Upload a PDF -> saves original to UPLOADS_BY_TOOL['pdf_decrypter']
    Encrypts to DOWNLOADS_BY_TOOL['pdf_decrypter'] and returns the encrypted file.
    """
    error = None
    message = None

    # Ensure the tool keys exist
    UPLOAD_KEY = "pdf_encrypter"
    DOWNLOAD_KEY = "pdf_encrypter"

    upload_base: Path = UPLOADS_BY_TOOL.get(UPLOAD_KEY)
    download_base: Path = DOWNLOADS_BY_TOOL.get(DOWNLOAD_KEY)

    if request.method == "POST":
        uploaded = request.files.get("pdf_file")
        password = (request.form.get("password") or "").strip()
        owner_pw = (request.form.get("owner_password") or "").strip()
        output_name = (request.form.get("output_name") or "").strip()

        # Basic validations
        if not uploaded or uploaded.filename == "":
            error = "No file uploaded."
            return render_template("pdf_encrypter.html", error=error)

        if not password:
            error = "You must supply a password to encrypt the PDF."
            return render_template("pdf_encrypter.html", error=error)

        # Ensure upload/download directories exist
        try:
            if not upload_base:
                raise RuntimeError("Server upload directory not configured for PDF encrypter.")
            if not download_base:
                raise RuntimeError("Server download directory not configured for PDF encrypter.")
            upload_base.mkdir(parents=True, exist_ok=True)
            download_base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            error = f"Server misconfiguration: {e}"
            return render_template("pdf_encrypter.html", error=error)

        # Secure the filename and save the uploaded file into uploads/
        original_name = secure_filename(uploaded.filename) or "uploaded.pdf"
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        saved_input_name = f"{timestamp}_{original_name}"
        input_path = upload_base / saved_input_name
        try:
            uploaded.save(str(input_path))
        except Exception as e:
            error = f"Failed to save uploaded file: {e}"
            return render_template("pdf_encrypter.html", error=error)

        # Decide output filename
        if not output_name:
            output_name_safe = f"encrypted_{original_name}"
        else:
            output_name_safe = secure_filename(output_name)
        if not output_name_safe.lower().endswith(".pdf"):
            output_name_safe += ".pdf"
        # avoid overwrite by adding timestamp if file exists
        output_path = download_base / output_name_safe
        if output_path.exists():
            output_path = download_base / f"{timestamp}_{output_name_safe}"

        # Read input bytes (from the saved file)
        try:
            with open(input_path, "rb") as fh:
                input_bytes = fh.read()
        except Exception as e:
            error = f"Failed to read uploaded file: {e}"
            return render_template("pdf_encrypter.html", error=error)

        # Try pikepdf first (AES), then PyPDF2 fallback
        try:
            if _HAS_PIKEPDF:
                try:
                    with pikepdf.Pdf.open(io.BytesIO(input_bytes)) as pdf:
                        # If owner_pw is empty, generate one so permissions behave predictably
                        owner_used = owner_pw if owner_pw else pikepdf._helpers.generate_password()
                        pdf.save(
                            str(output_path),
                            encryption=pikepdf.Encryption(
                                user=password,
                                owner=owner_used,
                                R=4,  # AES-128; change if you want different strength
                                allow=pikepdf.Permissions(extract=False),
                            ),
                        )
                except Exception as e_pike:
                    # fallback to PyPDF2 if pikepdf fails for this file
                    if not _HAS_PYPDF2:
                        raise RuntimeError(f"pikepdf failed ({e_pike}) and PyPDF2 not available")
                    # else fall through to PyPDF2 block below by raising a sentinel
                    raise
            else:
                # No pikepdf -> use PyPDF2
                raise RuntimeError("pikepdf not available")
        except Exception:
            # PyPDF2 fallback
            if _HAS_PYPDF2:
                try:
                    reader = PdfReader(io.BytesIO(input_bytes))
                    writer = PdfWriter()
                    for p in reader.pages:
                        writer.add_page(p)
                    # writer.encrypt(user_pwd, owner_pwd=None, use_128bit=True)
                    # modern PyPDF2 uses keyword args (but some older versions differ)
                    try:
                        writer.encrypt(user_pwd=password, owner_pwd=(owner_pw or None), use_128bit=True)
                    except TypeError:
                        # fallback for older PyPDF2 API
                        writer.encrypt(password, owner_pw or None, use_128bit=True)
                    with open(output_path, "wb") as out_f:
                        writer.write(out_f)
                except Exception as e_p2:
                    error = f"Encryption failed (PyPDF2): {e_p2}"
                    return render_template("pdf_encrypter.html", error=error)
            else:
                error = "Server missing PDF libraries. Install pikepdf or PyPDF2."
                return render_template("pdf_encrypter.html", error=error)

        # At this point output_path should exist
        if not output_path.exists():
            error = "Encryption process completed but output file was not created."
            return render_template("pdf_encrypter.html", error=error)

        # Optionally flash a message and return the file for download.
        flash(f"Encrypted PDF saved to downloads: {output_path.name}", "success")

        # return the file as attachment while keeping the file on the server
        return send_file(
            str(output_path),
            as_attachment=True,
            download_name=output_path.name,
            mimetype="application/pdf",
        )

    # GET -> show template (you can pass counts if your template expects them)
    try:
        counts = _count_dict() if "_count_dict" in globals() else {}
    except Exception:
        counts = {}
    return render_template("pdf_encrypter.html", error=error, message=message, counts=counts)


def _is_pdf(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_PDF_EXT


def _safe_output_name(name: str) -> str:
    """
    Normalize the output file name, enforcing .pdf extension and a reasonable base.
    """
    name = (name or "").strip()
    if not name:
        # default with timestamp
        name = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    else:
        # sanitize
        name = secure_filename(name)
        if not name:
            name = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        # ensure .pdf
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
    return name


# -----------------------------
# PDF combiner Page
# -----------------------------
@app.route("/pdf_combiner", methods=["GET", "POST"])
def pdf_combiner():
    """
    UI + handler for combining PDFs.
    - Uploads go to uploads/pdf_combiner/
    - Merged output goes to downloads/pdf_combiner/
    """
    tool_slug = "pdf_combiner"
    uploads_folder = UPLOAD_DIR / tool_slug
    downloads_folder = DOWNLOAD_DIR / tool_slug
    uploads_folder.mkdir(parents=True, exist_ok=True)
    downloads_folder.mkdir(parents=True, exist_ok=True)

    if request.method == "GET":
        return render_template("pdf_combiner.html", max_files=MAX_FILES)

    # How many files?
    try:
        n = int(request.form.get("num_pdfs", "2"))
    except ValueError:
        n = 2
    n = max(2, min(MAX_FILES, n))

    uploaded_paths = []
    errors = []

    # Collect files in order: pdf_1 ... pdf_n
    for i in range(1, n + 1):
        f = request.files.get(f"pdf_{i}")
        if not f or not f.filename.strip():
            errors.append(f"Missing file for PDF {i}.")
            continue

        fname = secure_filename(f.filename)
        if Path(fname).suffix.lower() != ".pdf":
            errors.append(f"File {i} must be a .pdf: got '{fname}'.")
            continue

        dst = uploads_folder / fname
        if dst.exists():
            dst = uploads_folder / f"{dst.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        f.save(dst)
        uploaded_paths.append(dst)

    if errors:
        return render_template("pdf_combiner.html", max_files=MAX_FILES, error=" ".join(errors))

    if len(uploaded_paths) < 2:
        return render_template("pdf_combiner.html", max_files=MAX_FILES, error="Please provide at least two PDFs.")

    # Merge
    writer = PdfWriter()
    try:
        for path in uploaded_paths:
            reader = PdfReader(str(path))
            for page in reader.pages:
                writer.add_page(page)
    except Exception as e:
        return render_template("pdf_combiner.html", max_files=MAX_FILES, error=f"Failed to merge: {e}")

    # Output name + write to downloads/pdf_combiner/
    out_name = _safe_output_name(request.form.get("output_name", ""))
    out_path = downloads_folder / out_name
    try:
        with out_path.open("wb") as fh:
            writer.write(fh)
    except Exception as e:
        return render_template("pdf_combiner.html", max_files=MAX_FILES, error=f"Failed to write output: {e}")

    # Immediately download (a copy remains in downloads/pdf_combiner/)
    return send_file(str(out_path),
                     mimetype="application/pdf",
                     as_attachment=True,
                     download_name=out_name,
                     max_age=0)


def _ensure_tool_dirs(tool_key: str) -> tuple[Path, Path]:
    """Return (upload_dir, download_dir) for a tool, creating them if needed."""
    up = UPLOADS_BY_TOOL.get(tool_key)
    down = DOWNLOADS_BY_TOOL.get(tool_key)
    if not up:
        raise RuntimeError(f"Server upload directory not configured for {tool_key}.")
    if not down:
        raise RuntimeError(f"Server download directory not configured for {tool_key}.")
    up.mkdir(parents=True, exist_ok=True)
    down.mkdir(parents=True, exist_ok=True)
    return up, down


def _is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMG_EXTS


def image_to_sketch(in_path: Path, out_path: Path, blur_radius: int = 15, boost_contrast: bool = False) -> None:
    """
    Convert image to a pencil-style sketch using a color-dodge blend:
      sketch = gray * 255 / (255 - blur(gray))
    Writes exactly to `out_path` (caller decides extension; we use .png).
    """
    with Image.open(in_path) as im:
        im = im.convert("RGB")
        gray = ImageOps.grayscale(im)

        if boost_contrast:
            gray = ImageOps.autocontrast(gray, cutoff=1)

        blur = gray.filter(ImageFilter.GaussianBlur(radius=max(1, int(blur_radius))))

        g = np.array(gray, dtype=np.float32)
        b = np.array(blur, dtype=np.float32)

        denom = 255.0 - b
        denom[denom < 1] = 1  # avoid division by zero
        dodge = (g * 255.0) / denom
        dodge = np.clip(dodge, 0, 255).astype(np.uint8)

        sketch = Image.fromarray(dodge, mode="L")
        if boost_contrast:
            sketch = ImageOps.autocontrast(sketch, cutoff=1)

        # Save exactly to requested path (caller enforces .png)
        out_path = out_path.with_suffix(".png")
        sketch.save(out_path, format="PNG")


# -------------------------
# Image to Sketch Converter
# -------------------------
@app.route("/image_sketch", methods=["GET", "POST"])
def image_sketch():
    """
    Upload an image -> save the original to uploads/image_sketcher/.
    Convert to sketch -> save PNG to downloads/image_sketcher/ and return it.
    Files are kept on disk so dashboard counters update.
    """
    error = None
    message = None

    TOOL_KEY = "image_sketch"

    if request.method == "POST":
        uploaded = request.files.get("image_file")
        requested_out = (request.form.get("output_name") or "").strip()
        boost = bool(request.form.get("high_contrast"))
        try:
            blur_radius = int(request.form.get("blur_radius", 15))
        except ValueError:
            blur_radius = 15

        # Basic validations
        if not uploaded or uploaded.filename.strip() == "":
            error = "Please choose an image file."
            return render_template("image_sketch.html", error=error)

        if not _is_allowed_image(uploaded.filename):
            error = "Unsupported file type. Please upload PNG/JPG/JPEG/WEBP/BMP."
            return render_template("image_sketch.html", error=error)

        # Ensure directories
        try:
            upload_base, download_base = _ensure_tool_dirs(TOOL_KEY)
        except Exception as e:
            error = f"Server misconfiguration: {e}"
            return render_template("image_sketch.html", error=error)

        # Save upload with timestamp (like pdf_encrypter)
        original_name = secure_filename(uploaded.filename) or "uploaded_image.png"
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        saved_input_name = f"{timestamp}_{original_name}"
        input_path = upload_base / saved_input_name
        try:
            uploaded.save(str(input_path))
        except Exception as e:
            error = f"Failed to save uploaded file: {e}"
            return render_template("image_sketch.html", error=error)

        # Decide output file name (always .png)
        if requested_out:
            out_stem = Path(requested_out).stem  # strip user extension
            output_name_safe = f"{secure_filename(out_stem)}.png"
        else:
            output_name_safe = f"{Path(original_name).stem}_sketch.png"

        output_path = download_base / output_name_safe
        if output_path.exists():
            output_path = download_base / f"{timestamp}_{output_name_safe}"

        # Convert
        try:
            image_to_sketch(input_path, output_path, blur_radius=blur_radius, boost_contrast=boost)
        except Exception as e:
            try:
                if output_path.exists():
                    output_path.unlink()
            except:
                pass
            error = f"Failed to convert image: {e}"
            return render_template("image_sketch.html", error=error)

        if not output_path.exists():
            error = "Sketch process completed but output file was not created."
            return render_template("image_sketch.html", error=error)

        flash(f"Sketch saved to downloads: {output_path.name}", "success")
        return send_file(
            str(output_path),
            as_attachment=True,
            download_name=output_path.name,
            mimetype="image/png",
        )

    # GET
    try:
        counts = _count_dict() if "_count_dict" in globals() else {}
    except Exception:
        counts = {}
    return render_template("image_sketch.html", error=error, message=message, counts=counts)


def _is_allowed_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_VIDEO_EXTS

_time_re = re.compile(
    r"""^\s*
    (?:
      (?:(\d+):)?        # hours (optional)
      (?:(\d{1,2}):)?    # minutes (optional)
      (\d+(?:\.\d+)?)    # seconds (required, may be float)
    )
    \s*$""",
    re.X,
)


def parse_timecode(s: str) -> float:
    """
    Accepts SS, MM:SS, or HH:MM:SS(.ms) and returns seconds as float.
    Examples: '21' -> 21.0; '1:05' -> 65.0; '00:01:05.5' -> 65.5
    """
    s = s.strip()
    if not s:
        raise ValueError("Empty timecode")
    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])
    elif len(parts) == 2:
        m, sec = parts
        return int(m) * 60 + float(sec)
    elif len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)
    else:
        m = _time_re.match(s)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            ss = float(m.group(3))
            return h * 3600 + mm * 60 + ss
        raise ValueError(f"Invalid time format: {s}")


def ffprobe_duration_seconds(in_path: Path) -> float:
    """
    Returns media duration in seconds using ffprobe.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(in_path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def remove_segment_with_concat(in_path: Path, start_s: float, end_s: float, out_path: Path) -> None:
    """
    Robust approach that re-encodes using filter_complex:
      - Create two segments: [0, start) and (end, EOF]
      - Concat them back together.
    This is resilient to keyframe boundaries and mixed codecs/containers.
    """
    # Build filter graph
    filter_graph = (
        f"[0:v]trim=end={start_s},setpts=PTS-STARTPTS[v0];"
        f"[0:a]atrim=end={start_s},asetpts=PTS-STARTPTS[a0];"
        f"[0:v]trim=start={end_s},setpts=PTS-STARTPTS[v1];"
        f"[0:a]atrim=start={end_s},asetpts=PTS-STARTPTS[a1];"
        f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(in_path),
        "-filter_complex", filter_graph,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",          # re-encode for compatibility
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-preset", "veryfast",
        "-crf", "20",
        str(out_path),
    ]
    subprocess.check_call(cmd)


@app.route("/video_cropper", methods=["GET", "POST"])
def video_cropper():
    if request.method == "GET":
        return render_template("video_cropper.html")

    # POST
    file = request.files.get("video")
    start_time = (request.form.get("start_time") or "").strip()
    end_time = (request.form.get("end_time") or "").strip()
    output_name = (request.form.get("output_name") or "").strip()

    if not file or file.filename == "":
        return render_template("video_cropper.html", error="Please choose a video file.")

    if not _is_allowed_video(file.filename):
        return render_template("video_cropper.html", error="Unsupported video type.")

    try:
        start_s = parse_timecode(start_time)
        end_s = parse_timecode(end_time)
    except ValueError as e:
        return render_template("video_cropper.html", error=str(e))

    if end_s <= start_s:
        return render_template("video_cropper.html", error="End time must be greater than start time.")

    # Save upload
    original_name = secure_filename(file.filename)
    upload_path = UPLOAD_DIR / "video_cropper" / original_name
    file.save(upload_path)  # <-- uploaded video saved to uploads/

    # Validate against duration
    try:
        dur = ffprobe_duration_seconds(upload_path)
    except Exception:
        dur = None
    if dur is not None and (start_s < 0 or end_s > dur):
        return render_template("video_cropper.html", error=f"Times must be within video duration ({dur:.2f}s).")

    # Build output name/path
    stem = Path(original_name).stem
    ext = ".mp4"  # Normalize to mp4 for compatibility
    if output_name:
        out_name = secure_filename(output_name)
        if not Path(out_name).suffix:
            out_name += ext
    else:
        # Example: myclip_cropped_0.0-21.0_20250923-130501.mp4
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_name = f"{stem}_cropped_{start_s:g}-{end_s:g}_{ts}{ext}"

    out_path = DOWNLOAD_DIR / "video_cropper" / out_name

    try:
        remove_segment_with_concat(upload_path, start_s, end_s, out_path)
    except subprocess.CalledProcessError as e:
        return render_template("video_cropper.html", error=f"ffmpeg failed: {e}")
    except Exception as e:
        return render_template("video_cropper.html", error=str(e))

    # Success: serve file as download and also leave it in downloads/
    return send_file(out_path, as_attachment=True, download_name=out_name)


def _is_allowed_media(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_MEDIA_EXTS


def _s_to_hms(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@app.route("/audio_to_text", methods=["GET", "POST"])
def audio_to_text():
    if request.method == "GET":
        return render_template("audio_to_text.html")

    # Detect AJAX (fetch) vs classic form submit
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # POST
    f = request.files.get("media")
    if not f or not f.filename:
        msg = "Please choose an audio/video file."
        if is_xhr:
            return jsonify(ok=False, error=msg), 400
        flash(msg, "danger")
        return redirect(request.url)

    if not _is_allowed_media(f.filename):
        msg = "Unsupported file type."
        if is_xhr:
            return jsonify(ok=False, error=msg), 400
        flash(msg, "danger")
        return redirect(request.url)

    model_size = request.form.get("model_size", "small")
    translate = bool(request.form.get("translate"))
    output_name = (request.form.get("output_name") or "").strip()

    # Save upload
    safe_name = secure_filename(f.filename)
    in_path = (UPLOADS_BY_TOOL["audio_to_text"] / safe_name).resolve()
    f.save(in_path)

    # --- Transcribe with faster-whisper ---
    try:
        from faster_whisper import WhisperModel
    except Exception:
        msg = "Server missing dependency: faster-whisper. Add it to requirements.txt."
        if is_xhr:
            return jsonify(ok=False, error=msg), 500
        flash(msg, "danger")
        return redirect(request.url)

    # Prefer GPU if available (toggle via env); else CPU
    compute_type = "int8_float16" if os.getenv("WHISPER_INT8", "0") == "1" else "float16"
    try:
        model = WhisperModel(
            model_size,
            device="cuda" if os.getenv("WHISPER_CUDA", "0") == "1" else "cpu",
            compute_type=compute_type
        )
    except Exception:
        model = WhisperModel(model_size, device="cpu", compute_type="float32")

    segments, info = model.transcribe(
        str(in_path),
        language=None,  # auto-detect
        task="translate" if translate else "transcribe",
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
    )

    # Build per-second bins from word timestamps
    second_map = {}   # int second -> list[str]
    detected_lang = getattr(info, "language", None)

    for seg in segments:
        if not getattr(seg, "words", None):
            sec = int(math.floor(seg.start)) if seg.start is not None else 0
            second_map.setdefault(sec, []).append(seg.text.strip())
            continue

        for w in seg.words:
            if w.start is None or not w.word:
                continue
            sec = int(math.floor(w.start))
            second_map.setdefault(sec, []).append(w.word)

    if not second_map:
        msg = "No speech detected."
        if is_xhr:
            return jsonify(ok=False, error=msg), 200
        flash(msg, "warning")
        return redirect(request.url)

    # Assemble lines: "HH:MM:SS  text"
    max_second = max(second_map.keys())
    lines = []
    for s in range(0, max_second + 1):
        words = second_map.get(s, [])
        if not words:
            continue
        text = " ".join(words)
        text = (text.replace(" ,", ",").replace(" .", ".")
                    .replace(" !", "!").replace(" ?", "?")
                    .replace(" :", ":").replace(" ;", ";"))
        lines.append(f"{_s_to_hms(s)}  {text.strip()}")

    # --- Write PDF to the tool's downloads folder ---
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_name and not output_name.lower().endswith(".pdf"):
        output_name += ".pdf"
    out_pdf = (DOWNLOADS_BY_TOOL["audio_to_text"]
               / (output_name or f"{in_path.stem}_transcript_{ts}.pdf")).resolve()

    # Try a monospaced font; fallback to Helvetica
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSansMono",
                                       "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
        font_name = "DejaVuSansMono"
    except Exception:
        font_name = "Helvetica"

    c = canvas.Canvas(str(out_pdf), pagesize=letter)
    width, height = letter
    margin = 0.75 * inch
    y = height - margin

    input_filename = in_path.name
    title = f"Second-by-Second Transcript — {input_filename} ({detected_lang or 'auto'})"
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, title)
    y -= 0.4 * inch

    c.setFont(font_name, 10)
    line_height = 13
    for line in lines:
        if y < margin:
            c.showPage()
            y = height - margin
            c.setFont(font_name, 10)
        c.drawString(margin, y, line)
        y -= line_height

    c.showPage()
    c.save()

    # Direct download behavior
    download_url = url_for("dl_download", tool="audio_to_text", filename=out_pdf.name)

    if is_xhr:
        # Return JSON so the page can stay put and JS can trigger a download + update counts
        return jsonify({
            "ok": True,
            "download_url": download_url,
            "filename": out_pdf.name,
            "counts": {
                "audio_to_text": len(list_files(DOWNLOADS_BY_TOOL["audio_to_text"]))
            }
        })

    # Non-AJAX: stream file as attachment immediately (browser saves to user's default location)
    return send_from_directory(DOWNLOADS_BY_TOOL["audio_to_text"], out_pdf.name, as_attachment=True)


# --- add near your other constants ---
TOOL_KEY = "image_background_remover"
TOOL_UPLOAD_DIR = UPLOAD_DIR / TOOL_KEY
TOOL_DOWNLOAD_DIR = DOWNLOAD_DIR / TOOL_KEY
TOOL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TOOL_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMG_EXTS


# -----------------------
# Utilities
# -----------------------
def _safe_png_name(orig_name: str, override: Optional[str]) -> str:
    """
    Build output filename (always .png).
    If override provided, ensure it ends with .png.
    Otherwise use <basename>_no-bg.png
    """
    if override:
        name = override.strip()
        if not name.lower().endswith(".png"):
            name += ".png"
        return secure_filename(name)

    base = Path(secure_filename(orig_name)).stem
    return f"{base}_no-bg.png"


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """
    Convert '#RRGGBB' or 'RRGGBB' to (R,G,B).
    """
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        # default to white if malformed
        return (255, 255, 255)
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b)


def remove_background_chroma(
    im: Image.Image,
    bg_rgb: Tuple[int, int, int],
    tol: int = 25,
    feather: bool = False
) -> Image.Image:
    """
    Remove pixels near 'bg_rgb' with tolerance 'tol'.
    Returns RGBA image with transparency where removed.
    """
    # Ensure RGB for distance math
    rgb = im.convert("RGB")
    arr = np.asarray(rgb, dtype=np.int16)  # H,W,3
    br, bg, bb = bg_rgb

    # Euclidean distance to background color
    dist = np.sqrt(
        (arr[..., 0] - br) ** 2 +
        (arr[..., 1] - bg) ** 2 +
        (arr[..., 2] - bb) ** 2
    )

    # Build mask: 255 where background, else 0
    mask = (dist <= max(0, int(tol))).astype(np.uint8) * 255
    mask_img = Image.fromarray(mask, mode="L")

    if feather:
        # soften edges
        mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=2))

    # Alpha = 255 - mask (remove bg -> alpha=0)
    alpha = ImageOps.invert(mask_img)

    rgba = rgb.copy()
    rgba.putalpha(alpha)
    return rgba


def remove_background_auto(im: Image.Image, feather: bool = False) -> Image.Image:
    """
    ML-based background removal via rembg (U^2-Net).
    Returns RGBA.
    """
    if not _HAS_REMBG:
        raise RuntimeError(
            "Auto mode requires 'rembg' (pip install rembg). "
            "Switch to 'Chroma key' in the form if you prefer not to install it."
        )

    arr = np.asarray(im.convert("RGBA"))
    out = rembg_remove(arr)
    rgba = Image.fromarray(out, mode="RGBA")

    if feather:
        # a gentle alpha blur on edges
        a = rgba.split()[-1]
        a = a.filter(ImageFilter.GaussianBlur(radius=1.2))
        rgba.putalpha(a)
    return rgba


# -----------------------
# Routes
# -----------------------
@app.route("/downloads/<path:filename>")
def serve_download(filename):
    # Serves files from downloads/ for direct link in the template
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route("/image_background_remover", methods=["GET", "POST"])
def image_background_remover():
    """
    GET: render the form
    POST: save original -> uploads/<tool>/ ; process ; save PNG -> downloads/<tool>/
          then AUTO-DOWNLOAD the new PNG (as attachment)
    """
    if request.method == "GET":
        # plain render (no success flash/link)
        return render_template("image_background_remover.html")

    # ---- POST ----
    f = request.files.get("image_file")
    if not f or not f.filename:
        return render_template("image_background_remover.html",
                               error="Please choose an image file.")

    if not _is_allowed_image(f.filename):
        return render_template("image_background_remover.html",
                               error="Unsupported image format. Use PNG, JPG, JPEG, WEBP, or BMP.")

    # Save original to uploads/<tool> (prefix with short uuid to avoid collisions)
    from werkzeug.utils import secure_filename
    import uuid
    orig_name = secure_filename(f.filename)
    up_name = f"{uuid.uuid4().hex[:8]}_{orig_name}"
    up_path = TOOL_UPLOAD_DIR / up_name
    f.save(up_path)

    # Read options
    output_name = _safe_png_name(orig_name, request.form.get("output_name"))
    method = (request.form.get("method") or "auto").lower().strip()
    feather_edges = bool(request.form.get("feather_edges"))

    # Process
    try:
        from PIL import Image
        with Image.open(up_path) as im:
            if method == "chroma":
                hex_color = request.form.get("chroma_color", "#ffffff")
                tol = int(request.form.get("chroma_tol", "25") or "25")
                out_im = remove_background_chroma(
                    im, bg_rgb=_hex_to_rgb(hex_color), tol=tol, feather=feather_edges
                )
            else:
                out_im = remove_background_auto(im, feather=feather_edges)

            out_im = out_im.convert("RGBA")

        # Save to downloads/<tool>
        out_name = f"{uuid.uuid4().hex[:6]}_{output_name}"
        out_path = TOOL_DOWNLOAD_DIR / out_name
        out_im.save(out_path, format="PNG")

    except RuntimeError as e:
        # e.g., 'rembg' not installed for Auto mode
        return render_template("image_background_remover.html", error=str(e))
    except Exception as e:
        return render_template("image_background_remover.html", error=f"Failed to process image: {e}")

    # ---- AUTO-DOWNLOAD ----
    # Return the file directly so the browser downloads it without showing a success flash.
    # Also increments your Downloads counter because it now exists under downloads/<tool>.
    return send_from_directory(TOOL_DOWNLOAD_DIR, out_name, as_attachment=True, download_name=out_name)
    

# -------------------------
# Uploads & Downloads library (tool cards + per-tool views)
# -------------------------
def _count_dict():
    return {
        "pdf_decrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_decrypter"])),
        "pdf_encrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_encrypter"])),
        "pdf_combiner": len(list_files(DOWNLOADS_BY_TOOL["pdf_combiner"])),
        "yt_vid_downloader": len(list_files(DOWNLOADS_BY_TOOL["yt_vid_downloader"])),
        "video_cropper": len(list_files(DOWNLOADS_BY_TOOL["video_cropper"])),
        "audio_to_text": len(list_files(DOWNLOADS_BY_TOOL["audio_to_text"])),
        "image_combiner": len(list_files(DOWNLOADS_BY_TOOL["image_combiner"])),
        "image_sketch": len(list_files(DOWNLOADS_BY_TOOL["image_sketch"])),
        "image_background_remover": len(list_files(DOWNLOADS_BY_TOOL["image_background_remover"])),
    }


@app.route("/uploads")
def uploads_index():
    counts = {
        "pdf_decrypter": len(list_files(UPLOADS_BY_TOOL["pdf_decrypter"])),
        "pdf_encrypter": len(list_files(UPLOADS_BY_TOOL["pdf_encrypter"])),
        "pdf_combiner": len(list_files(UPLOADS_BY_TOOL["pdf_combiner"])),
        "yt_vid_downloader": len(list_files(UPLOADS_BY_TOOL["yt_vid_downloader"])),
        "video_cropper": len(list_files(UPLOADS_BY_TOOL["video_cropper"])), 
        "audio_to_text": len(list_files(UPLOADS_BY_TOOL["audio_to_text"])), 
        "image_combiner": len(list_files(UPLOADS_BY_TOOL["image_combiner"])),
        "image_sketch": len(list_files(UPLOADS_BY_TOOL["image_sketch"])),
        "image_background_remover": len(list_files(UPLOADS_BY_TOOL["image_background_remover"])),
    }
    return render_template("uploads.html", tool=None, counts=counts, files=[])


@app.route("/uploads/<tool>")
def uploads_tool(tool):
    if tool not in UPLOADS_BY_TOOL:
        flash("Unknown uploads category.", "warning")
        return redirect(url_for("uploads_index"))
    files = list_files(UPLOADS_BY_TOOL[tool])
    counts = {
        "pdf_decrypter": len(list_files(UPLOADS_BY_TOOL["pdf_decrypter"])),
        "pdf_encrypter": len(list_files(UPLOADS_BY_TOOL["pdf_encrypter"])),
        "pdf_combiner": len(list_files(UPLOADS_BY_TOOL["pdf_combiner"])),
        "yt_vid_downloader": len(list_files(UPLOADS_BY_TOOL["yt_vid_downloader"])),
        "video_cropper": len(list_files(UPLOADS_BY_TOOL["video_cropper"])),  
        "audio_to_text": len(list_files(UPLOADS_BY_TOOL["audio_to_text"])),
        "image_combiner": len(list_files(UPLOADS_BY_TOOL["image_combiner"])),
        "image_sketch": len(list_files(UPLOADS_BY_TOOL["image_sketch"])),
        "image_background_remover": len(list_files(UPLOADS_BY_TOOL["image_background_remover"])),
    }
    return render_template("uploads.html", tool=tool, counts=counts, files=files)


@app.route("/uploads/<tool>/<path:filename>")
def view_upload(tool, filename):
    base = UPLOADS_BY_TOOL.get(tool)
    if not base:
        return ("Not found", 404)
    return send_from_directory(base, filename, as_attachment=False)


@app.route("/uploads/<tool>/<path:filename>/download")
def dl_upload(tool, filename):
    base = UPLOADS_BY_TOOL.get(tool)
    if not base:
        return ("Not found", 404)
    return send_from_directory(base, filename, as_attachment=True)


@app.route("/uploads/<tool>/<path:filename>/delete", methods=["POST"])
def del_upload(tool, filename):
    base = UPLOADS_BY_TOOL.get(tool)
    if not base:
        flash("Unknown uploads category.", "danger")
        return redirect(url_for("uploads_index"))
    fp = (base / filename).resolve()
    try:
        fp.unlink()
        flash(f"Deleted upload: {filename}", "success")
    except Exception as e:
        flash("File not found", "danger")
    return redirect(url_for("uploads_tool", tool=tool))


@app.route("/downloads")
def downloads_index():
    counts = {
        "pdf_decrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_decrypter"])),
        "pdf_encrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_encrypter"])),
        "pdf_combiner": len(list_files(DOWNLOADS_BY_TOOL["pdf_combiner"])),
        "yt_vid_downloader": len(list_files(DOWNLOADS_BY_TOOL["yt_vid_downloader"])),
        "video_cropper": len(list_files(DOWNLOADS_BY_TOOL["video_cropper"])),
        "audio_to_text": len(list_files(DOWNLOADS_BY_TOOL["audio_to_text"])),
        "image_combiner": len(list_files(DOWNLOADS_BY_TOOL["image_combiner"])),
        "image_sketch": len(list_files(DOWNLOADS_BY_TOOL["image_sketch"])),
        "image_background_remover": len(list_files(DOWNLOADS_BY_TOOL["image_background_remover"])),
    }
    return render_template("downloads.html", tool=None, counts=counts, files=[])


@app.route("/downloads/<tool>")
def downloads_tool(tool):
    if tool not in DOWNLOADS_BY_TOOL:
        flash("Unknown downloads category.", "warning")
        return redirect(url_for("downloads_index"))
    files = list_files(DOWNLOADS_BY_TOOL[tool])
    counts = {
        "pdf_decrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_decrypter"])),
        "pdf_encrypter": len(list_files(DOWNLOADS_BY_TOOL["pdf_encrypter"])),
        "pdf_combiner": len(list_files(DOWNLOADS_BY_TOOL["pdf_combiner"])),
        "yt_vid_downloader": len(list_files(DOWNLOADS_BY_TOOL["yt_vid_downloader"])),
        "video_cropper": len(list_files(DOWNLOADS_BY_TOOL["video_cropper"])),
        "audio_to_text": len(list_files(DOWNLOADS_BY_TOOL["audio_to_text"])),
        "image_combiner": len(list_files(DOWNLOADS_BY_TOOL["image_combiner"])),
        "image_sketch": len(list_files(DOWNLOADS_BY_TOOL["image_sketch"])),
        "image_background_remover": len(list_files(DOWNLOADS_BY_TOOL["image_background_remover"])),
    }
    return render_template("downloads.html", tool=tool, counts=counts, files=files)


@app.route("/downloads/<tool>/<path:filename>")
def view_download(tool, filename):
    base = DOWNLOADS_BY_TOOL.get(tool)
    if not base:
        return ("Not found", 404)
    return send_from_directory(base, filename, as_attachment=False)


@app.route("/downloads/<tool>/<path:filename>/download")
def dl_download(tool, filename):
    base = DOWNLOADS_BY_TOOL.get(tool)
    if not base:
        return ("Not found", 404)
    return send_from_directory(base, filename, as_attachment=True)


@app.route("/downloads/<tool>/<path:filename>/delete", methods=["POST"])
def del_download(tool, filename):
    base = DOWNLOADS_BY_TOOL.get(tool)
    if not base:
        flash("Unknown downloads category.", "danger")
        return redirect(url_for("downloads_index"))
    fp = (base / filename).resolve()
    try:
        fp.unlink()
        flash(f"Deleted download: {filename}", "success")
    except Exception as e:
        flash("File not found", "danger")
    return redirect(url_for("downloads_tool", tool=tool))


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    app.run(debug=True)
