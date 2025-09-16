from __future__ import annotations

import os
import subprocess
import shutil as _shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from shutil import which as sh_which

from werkzeug.utils import secure_filename
from PIL import Image
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, send_file, flash,
)

# Optional dependency for YouTube
try:
    import yt_dlp  # pip install yt-dlp
except Exception:
    yt_dlp = None

# Make imageio-ffmpeg's bundled ffmpeg visible (works on Railway too)
try:
    import imageio_ffmpeg as _iioff
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", _iioff.get_ffmpeg_exe())
except Exception:
    pass

def has_ffmpeg() -> bool:
    return bool(sh_which("ffmpeg") or os.environ.get("IMAGEIO_FFMPEG_EXE"))

# --- ffmpeg detection & setup ---
def _discover_ffmpeg_exe() -> str | None:
    """
    Return an absolute path to an ffmpeg executable if available.
    Tries system ffmpeg, then imageio-ffmpeg's bundled binary.
    """
    # System ffmpeg in PATH?
    p = sh_which("ffmpeg")
    if p:
        return p
    # Bundled ffmpeg from imageio-ffmpeg?
    try:
        import imageio_ffmpeg as _iioff
        return _iioff.get_ffmpeg_exe()
    except Exception:
        return None

FFMPEG_EXE = _discover_ffmpeg_exe()
if FFMPEG_EXE:
    # help libraries that read this env var
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", FFMPEG_EXE)

def has_ffmpeg() -> bool:
    """Actually run `ffmpeg -version` to be sure."""
    if not FFMPEG_EXE:
        return False
    try:
        out = subprocess.run(
            [FFMPEG_EXE, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
        )
        return out.returncode == 0
    except Exception:
        return False

def _yt_opts(base_out_no_ext: str, as_audio: bool) -> dict:
    """
    Build yt-dlp options. base_out_no_ext should be a path without extension.
    """
    opts = {
        "outtmpl": base_out_no_ext + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "merge_output_format": "mp4" if not as_audio else "mp3",
        "format": "bv*[ext=mp4]+ba[ext=mp3]/b[ext=mp4]/best",
        # prefer ffmpeg & tell yt-dlp exactly where it is
        "prefer_ffmpeg": True,
    }
    if FFMPEG_EXE:
        # yt-dlp accepts a dir OR a full path; both work.
        opts["ffmpeg_location"] = os.path.dirname(FFMPEG_EXE) \
                                  if os.path.sep in FFMPEG_EXE else FFMPEG_EXE
    if as_audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
        }]
    return opts

def download_youtube(url: str, out_name: str = "my_video.mp4", as_audio=False) -> Path:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp not installed")
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg not available on server")
    out_path = DOWNLOAD_DIR / out_name
    base = str(out_path.with_suffix(""))
    with yt_dlp.YoutubeDL(_yt_opts(base, as_audio)) as ydl:
        ydl.download([url])
    # find the actual produced file if extension differs
    produced = out_path if out_path.exists() else next(DOWNLOAD_DIR.glob(out_path.stem + ".*"))
    return produced


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = "dev-secret-key"  # replace for production


# -------------------------
# Storage layout
# -------------------------
BASE = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE / "uploads"
DOWNLOAD_DIR = BASE / "downloads"

UPLOADS_BY_TOOL = {
    "combine": UPLOAD_DIR / "combine",
    "youtube": UPLOAD_DIR / "youtube",
    #"activity_combiner": UPLOAD_DIR / "activity_combiner",
}
DOWNLOADS_BY_TOOL = {
    "combine": DOWNLOAD_DIR / "combine",
    "youtube": DOWNLOAD_DIR / "youtube",
    #"activity_combiner": DOWNLOAD_DIR / "activity_combiner",
}

# Ensure folders exist
for p in [UPLOAD_DIR, DOWNLOAD_DIR, *UPLOADS_BY_TOOL.values(), *DOWNLOADS_BY_TOOL.values()]:
    p.mkdir(parents=True, exist_ok=True)

ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_FILES = 10


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
# Combine Images
# -------------------------
@app.route("/combine", methods=["GET", "POST"])
def combine():
    if request.method == "GET":
        return render_template("combine.html", max_files=MAX_FILES)

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
            return render_template("combine.html", max_files=MAX_FILES)

        # Orientation (vertical/horizontal)
        orientation = (request.form.get("orientation") or "vertical").lower().strip()
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"

        # Save uploads
        up_dir = UPLOADS_BY_TOOL["combine"]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_paths: List[Path] = []
        for idx, f in enumerate(files, start=1):
            name = secure_filename(f.filename)
            base, ext = os.path.splitext(name)
            if ext.lower() not in ALLOWED_IMG_EXTS:
                flash(f"Unsupported file type: {name}", "danger")
                return render_template("combine.html", max_files=MAX_FILES)
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

        out_dir = DOWNLOADS_BY_TOOL["combine"]
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
        return render_template("combine.html", max_files=MAX_FILES)


# -------------------------
# YouTube Downloader
# -------------------------
# Assumes:
#   - yt_dlp is imported
#   - DOWNLOADS_BY_TOOL["youtube"] exists and points to a folder

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

@app.route("/youtube", methods=["GET", "POST"])
def youtube():
    """Download a YouTube URL as MP4 (video) or MP3 (audio-only) and auto-send to browser."""
    if request.method == "GET":
        return render_template("youtube.html")

    # --- POST begins ---
    if yt_dlp is None:
        flash("Missing dependency: yt-dlp. Run: pip install yt-dlp", "danger")
        return render_template("youtube.html")

    if sh_which("ffmpeg") is None:
        flash("Missing dependency: ffmpeg. Install it (e.g., brew install ffmpeg) and restart.", "danger")
        return render_template("youtube.html")

    video_url = (request.form.get("video_url") or "").strip()
    output_name = (request.form.get("output_name") or "").strip()
    fmt = (request.form.get("format") or "mp4").lower().strip()

    if not video_url:
        flash("Please paste a YouTube URL.", "warning")
        return render_template("youtube.html")

    # Normalize base filename
    safe = secure_filename(output_name) if output_name else "video"
    base, _ext = os.path.splitext(safe)
    final_base = base or "video"

    out_dir: Path = DOWNLOADS_BY_TOOL["youtube"]
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
            return render_template("youtube.html")

        path = expected if expected.exists() else max(
            out_dir.glob(f"{final_base}.*"), key=lambda p: p.stat().st_mtime, default=None
        )
        if not path or not path.exists():
            flash("We couldn't locate the downloaded file. Please try again.", "danger")
            return render_template("youtube.html")

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
        return render_template("youtube.html")

    saved = expected_mp4 if expected_mp4.exists() else newest_match()
    if not saved or not saved.exists():
        flash("We couldn't locate the downloaded file. Please try again.", "danger")
        return render_template("youtube.html")

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
            return render_template("youtube.html")

        saved = expected_mp4 if expected_mp4.exists() else newest_match()
        if not saved or not saved.exists():
            flash("We couldn't locate the re-encoded file.", "danger")
            return render_template("youtube.html")

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


# -------------------------
# Uploads & Downloads library (tool cards + per-tool views)
# -------------------------
def _count_dict():
    return {
        "combine": len(list_files(DOWNLOADS_BY_TOOL["combine"])),
        "youtube": len(list_files(DOWNLOADS_BY_TOOL["youtube"])),
        #"activity_combiner": len(list_files(DOWNLOADS_BY_TOOL["activity_combiner"])),
    }


@app.route("/uploads")
def uploads_index():
    counts = {
        "combine": len(list_files(UPLOADS_BY_TOOL["combine"])),
        "youtube": len(list_files(UPLOADS_BY_TOOL["youtube"])),
        #"activity_combiner": len(list_files(UPLOADS_BY_TOOL["activity_combiner"])),
    }
    return render_template("uploads.html", tool=None, counts=counts, files=[])


@app.route("/uploads/<tool>")
def uploads_tool(tool):
    if tool not in UPLOADS_BY_TOOL:
        flash("Unknown uploads category.", "warning")
        return redirect(url_for("uploads_index"))
    files = list_files(UPLOADS_BY_TOOL[tool])
    counts = {
        "combine": len(list_files(UPLOADS_BY_TOOL["combine"])),
        "youtube": len(list_files(UPLOADS_BY_TOOL["youtube"])),
        #"activity_combiner": len(list_files(UPLOADS_BY_TOOL["activity_combiner"])),
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
        "combine": len(list_files(DOWNLOADS_BY_TOOL["combine"])),
        "youtube": len(list_files(DOWNLOADS_BY_TOOL["youtube"])),
        #"activity_combiner": len(list_files(DOWNLOADS_BY_TOOL["activity_combiner"])),
    }
    return render_template("downloads.html", tool=None, counts=counts, files=[])


@app.route("/downloads/<tool>")
def downloads_tool(tool):
    if tool not in DOWNLOADS_BY_TOOL:
        flash("Unknown downloads category.", "warning")
        return redirect(url_for("downloads_index"))
    files = list_files(DOWNLOADS_BY_TOOL[tool])
    counts = {
        "combine": len(list_files(DOWNLOADS_BY_TOOL["combine"])),
        "youtube": len(list_files(DOWNLOADS_BY_TOOL["youtube"])),
        #"activity_combiner": len(list_files(DOWNLOADS_BY_TOOL["activity_combiner"])),
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







































































'''
# ===== Imports (place near your other imports) =====
import io
import os
import datetime as dt
from pathlib import Path
from typing import List, Tuple, Optional

from flask import request, send_file, render_template, flash, redirect, url_for, abort
from werkzeug.utils import secure_filename

# GPX/FIT helpers
import gpxpy
import gpxpy.gpx

try:
    from fitparse import FitFile
    _HAS_FITPARSE = True
except Exception:
    _HAS_FITPARSE = False

# ===== Configuration (adjust paths to match your app’s layout) =====
#BASE_DIR = Path(__file__).resolve().parent
#DOWNLOADS_DIR = BASE_DIR / "downloads" / "activities" / "combine"
#DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTS = {".gpx", ".fit"}


# ===== Utilities =====
def _is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTS

def _semicircles_to_degrees(semicircles: Optional[float]) -> Optional[float]:
    if semicircles is None:
        return None
    return float(semicircles) * (180.0 / 2**31)

def _read_gpx_to_trackpoints(fp: io.BytesIO) -> List[Tuple[Optional[float], Optional[float], Optional[float], Optional[dt.datetime]]]:
    """
    Returns a list of (lat, lon, ele, time) from a GPX file-like.
    """
    fp.seek(0)
    gpx = gpxpy.parse(fp.read().decode("utf-8", errors="ignore"))
    pts = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for p in seg.points:
                pts.append((p.latitude, p.longitude, p.elevation, p.time))
    # Also consider GPX routes/waypoints if tracks missing
    if not pts:
        for rte in gpx.routes:
            for p in rte.points:
                pts.append((p.latitude, p.longitude, p.elevation, None))
        for w in gpx.waypoints:
            pts.append((w.latitude, w.longitude, w.elevation, None))
    return pts

def _read_fit_to_trackpoints(fp: io.BytesIO) -> List[Tuple[Optional[float], Optional[float], Optional[float], Optional[dt.datetime]]]:
    """
    Parse FIT and return list of (lat, lon, ele, time). Requires fitparse.
    """
    if not _HAS_FITPARSE:
        raise RuntimeError("FIT parsing requires the 'fitparse' package.")
    fp.seek(0)
    fit = FitFile(fp)
    pts = []
    for record in fit.get_messages("record"):
        lat = lon = ele = time = None
        for d in record:
            name = d.name
            val = d.value
            if name == "position_lat":
                lat = _semicircles_to_degrees(val)
            elif name == "position_long":
                lon = _semicircles_to_degrees(val)
            elif name == "altitude":
                ele = float(val) if val is not None else None
            elif name == "timestamp":
                time = val if isinstance(val, dt.datetime) else None
        if lat is not None and lon is not None:
            pts.append((lat, lon, ele, time))
    return pts

def _collect_points_from_uploads(files) -> List[Tuple[Optional[float], Optional[float], Optional[float], Optional[dt.datetime]]]:
    """
    Accepts an iterable of Werkzeug FileStorage objects.
    Reads GPX and/or FIT and returns a single combined list of (lat, lon, ele, time),
    preserving per-file order, then concatenating in the order the user selected.
    """
    combined = []
    for f in files:
        if not f or f.filename == "":
            continue
        if not _is_allowed(f.filename):
            continue
        suffix = Path(f.filename).suffix.lower()
        buf = io.BytesIO(f.read())
        if suffix == ".gpx":
            combined.extend(_read_gpx_to_trackpoints(buf))
        elif suffix == ".fit":
            combined.extend(_read_fit_to_trackpoints(buf))
    # If there are timestamps, we *could* sort by time; by default, keep file order.
    return combined

def _build_gpx(points: List[Tuple[Optional[float], Optional[float], Optional[float], Optional[dt.datetime]]]) -> str:
    """
    Build a simple GPX 1.1 document with a single track and a single segment.
    """
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "Activity Combiner"
    track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for lat, lon, ele, t in points:
        if lat is None or lon is None:
            continue
        p = gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon, elevation=ele, time=t)
        seg.points.append(p)
    return gpx.to_xml()

# ===== Routes =====

@app.route("/activity-combiner", methods=["GET", "POST"])
def activity_combiner():
    """
    GET: show the Activity Combiner page
    POST: accept multiple GPX/FIT files, merge, and produce a single GPX
          (keeps copies of uploads in uploads/activity_combiner and
           writes the result to downloads/activity_combiner so badges update)
    """
    if request.method == "GET":
        return render_template("activity_combiner.html")

    # POST
    files = request.files.getlist("tracks")
    out_fmt = (request.form.get("format", "gpx") or "gpx").lower()  # 'gpx' or 'fit'
    output_name = (request.form.get("output_name") or "").strip()

    if not files or all((not f or f.filename == "") for f in files):
        flash("Please choose at least one GPX or FIT file.", "warning")
        return redirect(url_for("activity_combiner"))

    # Normalize output filename + extension
    if not output_name:
        output_name = "combined_activity"
    base, ext = os.path.splitext(output_name)
    desired_ext = f".{out_fmt}"
    if ext.lower() != desired_ext:
        output_name = f"{base or 'combined_activity'}{desired_ext}"

    uploads_dir = UPLOADS_BY_TOOL["activity_combiner"]
    downloads_dir = DOWNLOADS_BY_TOOL["activity_combiner"]
    uploads_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Save copies of the uploaded files into uploads/activity_combiner
        saved_any = False
        for f in files:
            if not f or not f.filename:
                continue
            fname = secure_filename(f.filename)
            if not fname:
                continue
            (uploads_dir / fname).write_bytes(f.read())
            saved_any = True
            # rewind the file stream so downstream readers can parse it
            f.stream.seek(0)

        if not saved_any:
            flash("No valid files were provided.", "warning")
            return redirect(url_for("activity_combiner"))

        # Collect points from the uploaded files (function you already have)
        points = _collect_points_from_uploads(files)
        if not points:
            flash("No GPS track points were found in the provided files.", "warning")
            return redirect(url_for("activity_combiner"))

        # We only emit GPX here (FIT export not available in this environment)
        if out_fmt == "fit":
            flash("Direct FIT export isn’t available here. Providing a GPX instead.", "info")
            output_name = f"{Path(output_name).stem}.gpx"

        # Build and save the GPX to downloads/activity_combiner so the counter updates
        xml = _build_gpx(points)
        out_path = downloads_dir / secure_filename(output_name)
        out_path.write_text(xml, encoding="utf-8")

        # Send the saved file (keeps a copy on disk for the downloads counter)
        return send_file(out_path, as_attachment=True, download_name=out_path.name)

    except Exception as e:
        flash(f"Failed to combine activities: {e}", "danger")
        return redirect(url_for("activity_combiner"))





from pathlib import Path
from flask import send_from_directory, url_for, render_template, abort, request

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads" / "activity_combiner"   # <- set this to where you actually save GPX
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def latest_combined_gpx(default_name: str = "are.gpx") -> str | None:
    """Return name of most recent .gpx in DOWNLOAD_DIR, or default_name if present, else None."""
    gpx_files = list(DOWNLOAD_DIR.glob("*.gpx"))
    if gpx_files:
        gpx_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return gpx_files[0].name
    default_path = DOWNLOAD_DIR / default_name
    return default_name if default_path.exists() else None


# Make `latest_combined` available in all templates so the button can enable
@app.context_processor
def inject_latest_combined():
    return {"latest_combined": latest_combined_gpx()}


# Serve combined files from the configured folder (no overlap with /downloads)
@app.route("/combined-gpx/<path:filename>")
def serve_combined_file(filename):
    safe_path = DOWNLOAD_DIR / filename
    if not safe_path.exists():
        abort(404)
    return send_from_directory(DOWNLOAD_DIR, filename)


@app.route("/tools/activity_combiner/view")
def view_combined_route():
    filename = request.args.get("filename") or latest_combined_gpx()
    if not filename:
        return render_template("map_viewer.html", filename=None,
                               error="No combined GPX found yet. Create one first.")
    if not (DOWNLOAD_DIR / filename).exists():
        return render_template("map_viewer.html", filename=None,
                               error=f"File not found: {filename}")

    # IMPORTANT: use the new /combined-gpx/... route
    gpx_url = url_for("serve_combined_file", filename=filename)
    return render_template("map_viewer.html", filename=filename, gpx_url=gpx_url)

'''
