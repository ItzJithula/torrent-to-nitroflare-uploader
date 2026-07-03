import logging
import sys
import zipfile
import shutil
import tempfile
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, Callable


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None,
                  max_size_mb: int = 10, backup_count: int = 5):
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
        root_logger.addHandler(file_handler)

    logging.getLogger("libtorrent").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    return root_logger


def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


def format_speed(speed_bytes_per_sec: float) -> str:
    return f"{format_size(int(speed_bytes_per_sec))}/s"


def format_time(seconds: int) -> str:
    if seconds < 0:
        return "∞"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def zip_folder(folder_path: Path, output_path: Optional[Path] = None, progress_callback: Optional[Callable] = None) -> Path:
    """
    Zip a folder into a single archive.
    Preserves the folder name as the archive name inside the zip.

    Args:
        folder_path: Path to the folder to zip.
        output_path: Optional output zip path. If None, uses <folder_name>.zip in the same parent dir.
        progress_callback: Optional callback(downloaded, total) for progress.

    Returns:
        Path to the created zip file.
    """
    folder_path = Path(folder_path)
    if not folder_path.exists() or not folder_path.is_dir():
        raise ValueError(f"Not a valid directory: {folder_path}")

    if output_path is None:
        output_path = folder_path.parent / f"{folder_path.name}.zip"

    files = [f for f in folder_path.rglob("*") if f.is_file()]
    total_size = sum(f.stat().st_size for f in files)
    done_size = 0

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            arcname = file_path.relative_to(folder_path.parent)  # preserves folder structure
            zf.write(file_path, arcname)
            done_size += file_path.stat().st_size
            if progress_callback:
                progress_callback({"downloaded": done_size, "total": total_size, "progress": (done_size / total_size) * 100})

    logger.info(f"Created zip archive: {output_path} ({format_size(total_size)})")
    return output_path
