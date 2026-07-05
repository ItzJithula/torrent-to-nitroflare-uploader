"""
HTTP Downloader — downloads files from direct HTTP(S) URLs without libtorrent.

Supports:
- Resumable downloads via HTTP Range headers
- Automatic retry with exponential backoff
- Progress callbacks
- Configurable chunk size and timeouts
- Graceful handling of very large files (streams to disk)
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional, Callable

import requests

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when a download fails after all retries."""
    pass


class ResumeNotSupportedError(DownloadError):
    """Raised when the server does not support Range requests for resume."""
    pass


class HTTPDownloader:
    """Downloads files from direct HTTP(S) URLs to local disk.

    Uses streaming downloads so that files of arbitrary size can be handled
    without loading the entire payload into memory.  Supports resuming
    interrupted downloads when the remote server honours Range headers.
    """

    def __init__(
        self,
        download_dir: str = "./downloads",
        timeout: int = 3600,
        retry_attempts: int = 3,
        retry_delay: float = 5.0,
        chunk_size: int = 8 * 1024 * 1024,  # 8 MiB
        buffer_size: int = 8 * 1024,         # 8 KiB for iter_content
    ):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.chunk_size = chunk_size
        self.buffer_size = buffer_size

    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        resume: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> Path:
        """Download *url* and return the local file path.

        Parameters
        ----------
        url:
            Direct HTTP(S) URL to download.
        filename:
            Override the output filename.  When *None* (the default) the name
            is derived from Content-Disposition or the last path segment of
            the URL.
        resume:
            Whether to attempt resuming a partially-downloaded file.
        progress_callback:
            Called periodically with ``{"downloaded": int, "total": int,
            "progress": float}``.

        Returns
        -------
        Path to the completed file on disk.
        """
        temp_path = None
        try:
            resolved_name, temp_path, already_downloaded = self._prepare_download(
                url, filename, resume
            )
            final_path = self.download_dir / resolved_name

            # If the file already exists and is complete, short-circuit.
            if not temp_path:
                logger.info("File already fully downloaded: %s", final_path)
                return final_path

            logger.info(
                "Downloading %s (%s already on disk)",
                url,
                _format_size(already_downloaded),
            )

            self._stream_to_disk(
                url, temp_path, already_downloaded, progress_callback
            )

            # Rename temp -> final
            if final_path.exists():
                final_path.unlink()
            temp_path.rename(final_path)

            logger.info("Download complete: %s (%s)", final_path, _format_size(final_path.stat().st_size))
            return final_path

        except Exception:
            # Clean up partial downloads on failure
            if temp_path and temp_path.exists():
                temp_path.unlink()
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_filename(self, url: str, response: requests.Response) -> str:
        """Determine the output filename from the response or URL."""
        cd = response.headers.get("content-disposition", "")
        if "filename=" in cd:
            return cd.split("filename=")[-1].strip('"\' ').split(";")[0]

        # Fall back to the last path segment of the URL
        filename = url.split("/")[-1].split("?")[0]
        if filename and "." in filename:
            return filename
        return "download"

    def _prepare_download(
        self, url: str, filename: Optional[str], resume: bool
    ):
        """Send a HEAD / conditional GET to learn the remote file size and
        figure out whether we can resume.

        Returns ``(resolved_name, temp_path_or_None, already_downloaded)``.
        When the temp path is *None* the file is already complete.
        """
        # Peek at headers first
        try:
            head_resp = requests.head(url, timeout=self.timeout, allow_redirects=True)
            head_resp.raise_for_status()
            total_size = int(head_resp.headers.get("content-length", 0))
            accept_ranges = head_resp.headers.get("accept-ranges", "").lower()
            resolved_name = filename or self._resolve_filename(
                url, head_resp
            )
        except requests.RequestException:
            # Some servers reject HEAD – fall back to a GET that we'll
            # cancel after reading the headers.
            resp = requests.get(url, stream=True, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            total_size = int(resp.headers.get("content-length", 0))
            accept_ranges = resp.headers.get("accept-ranges", "").lower()
            resolved_name = filename or self._resolve_filename(url, resp)
            resp.close()

        temp_path = self.download_dir / f".{resolved_name}.part"
        final_path = self.download_dir / resolved_name

        # If the final file exists and matches the expected size, skip entirely.
        if final_path.exists():
            if total_size > 0 and final_path.stat().st_size >= total_size:
                return resolved_name, None, total_size

        already_downloaded = 0
        if resume and temp_path.exists():
            already_downloaded = temp_path.stat().st_size
            if total_size > 0 and already_downloaded >= total_size:
                # The partial file is actually complete
                return resolved_name, None, total_size
            if already_downloaded > 0 and "bytes" not in accept_ranges:
                logger.warning(
                    "Server doesn't support Range requests; restarting from scratch"
                )
                already_downloaded = 0
                temp_path.unlink()

        return resolved_name, temp_path, already_downloaded

    def _stream_to_disk(
        self,
        url: str,
        temp_path: Path,
        offset: int,
        progress_callback: Optional[Callable],
    ):
        """Perform the actual HTTP GET (with optional Range header) and stream
        chunks to *temp_path*."""
        headers = {"Range": f"bytes={offset}-"} if offset > 0 else {}
        total_size = 0
        downloaded = offset

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                total_size = offset + int(
                    resp.headers.get("content-length", 0)
                )

                mode = "ab" if offset > 0 else "wb"
                with open(temp_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=self.buffer_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback({
                                    "downloaded": downloaded,
                                    "total": total_size,
                                    "progress": (
                                        (downloaded / total_size) * 100
                                        if total_size > 0
                                        else 0
                                    ),
                                })
                # Success – break out of the retry loop
                return

            except (requests.RequestException, OSError) as exc:
                logger.warning(
                    "Download attempt %d/%d failed: %s",
                    attempt,
                    self.retry_attempts,
                    exc,
                )
                if attempt < self.retry_attempts:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.info("Retrying in %.1f seconds…", delay)
                    time.sleep(delay)
                    # Update header for resume on next attempt
                    if temp_path.exists():
                        offset = temp_path.stat().st_size
                        headers = {"Range": f"bytes={offset}-"}
                    else:
                        offset = 0
                        headers = {}
                else:
                    raise DownloadError(
                        f"Download failed after {self.retry_attempts} attempts: {exc}"
                    ) from exc


def _format_size(size_bytes: int) -> str:
    """Return a human-readable size string."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    import math

    idx = min(int(math.floor(math.log(size_bytes, 1024))), len(units) - 1)
    value = size_bytes / (1024 ** idx)
    return f"{value:.2f} {units[idx]}"
