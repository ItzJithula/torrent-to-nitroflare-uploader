import os
import time
import logging
import requests
from pathlib import Path
from typing import Optional, Callable
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class NitroflareUploader:
    def __init__(self, api_key: str, timeout: int = 3600):
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = "https://nitroflare.com/api/"
        self.upload_server_url: Optional[str] = None

    def _get_upload_server(self) -> str:
        try:
            # Nitroflare's getServer endpoint returns a plain-text upload URL
            # (e.g. "https://s88.nitroflare.com:8443/index.php"), not JSON.
            response = requests.get(
                "https://nitroflare.com/plugins/fileupload/getServer",
                timeout=30,
            )
            response.raise_for_status()
            server_url = response.text.strip()

            if not server_url.startswith("http"):
                raise ValueError(f"Unexpected upload server response: {server_url!r}")

            self.upload_server_url = server_url
            logger.info(f"Got upload server: {server_url}")
            return server_url

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get upload server: {e}")
            raise

    def upload_file(self, file_path: str, progress_callback: Optional[Callable] = None) -> dict:
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not self.upload_server_url:
            self._get_upload_server()

        logger.info(f"Uploading file: {file_path.name} ({file_path.stat().st_size / (1024*1024):.2f} MB)")

        try:
            with open(file_path, "rb") as f:
                files = {"files": (file_path.name, f, "application/octet-stream")}
                data = {"user": self.api_key}

                response = requests.post(
                    self.upload_server_url,
                    files=files,
                    data=data,
                    timeout=self.timeout,
                    stream=True,
                )

                response.raise_for_status()

                result = response.json()
                logger.info(f"Upload response: {result}")

                # Nitroflare returns {"files": [{"name", "size", "type", "xxhash", "url"}]}
                files_list = result.get("files", [])
                if files_list:
                    file_info = files_list[0]
                    download_url = file_info.get("url")
                    return {
                        "status": "success",
                        "file": file_path.name,
                        "result": file_info,
                        "download_url": download_url,
                        "message": "Upload successful",
                    }

                # Fallback for legacy {"status": "OK", "result": {...}} format
                if result.get("status") == "OK":
                    return {
                        "status": "success",
                        "file": file_path.name,
                        "result": result.get("result", {}),
                        "download_url": result.get("result", {}).get("url"),
                        "message": result.get("message", "Upload successful"),
                    }

                error_msg = result.get("message", "Unknown error")
                logger.error(f"Upload failed: {error_msg}")
                return {
                    "status": "error",
                    "file": file_path.name,
                    "message": error_msg,
                    "raw_response": result,
                }

        except requests.exceptions.Timeout:
            logger.error(f"Upload timeout for {file_path.name}")
            return {
                "status": "timeout",
                "file": file_path.name,
                "message": f"Upload timed out after {self.timeout} seconds",
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Upload failed for {file_path.name}: {e}")
            return {
                "status": "error",
                "file": file_path.name,
                "message": str(e),
            }

    def upload_multiple(self, file_paths: list, progress_callback: Optional[Callable] = None) -> list:
        results = []
        total = len(file_paths)

        for idx, file_path in enumerate(file_paths, 1):
            logger.info(f"Uploading file {idx}/{total}: {Path(file_path).name}")

            if progress_callback:
                progress_callback({
                    "current": idx,
                    "total": total,
                    "file": Path(file_path).name,
                    "status": "uploading",
                })

            result = self.upload_file(file_path, progress_callback)
            result["index"] = idx
            results.append(result)

            if idx < total:
                time.sleep(1)

        return results
