import logging
import requests
import os
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class DirectLinkDownloader:
    """Downloads files from direct URLs (e.g., proxy tunnel links)."""

    def __init__(self, download_dir: str, timeout: int = 3600):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Path:
        """
        Download a file from a direct URL.
        Returns the path to the downloaded file.
        """
        logger.info(f"Starting download from: {url}")

        try:
            response = requests.get(url, stream=True, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            content_disposition = response.headers.get("content-disposition", "")
            if filename is None:
                if "filename=" in content_disposition:
                    filename = content_disposition.split("filename=")[-1].strip('"\' ')
                else:
                    filename = url.split("/")[-1].split("?")[0]
                    if not filename:
                        filename = "download"

            filepath = self.download_dir / filename
            downloaded = 0

            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback({
                                "downloaded": downloaded,
                                "total": total_size,
                                "progress": (downloaded / total_size) * 100,
                            })

            logger.info(f"Download complete: {filepath}")
            return filepath

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download from {url}: {e}")
            raise