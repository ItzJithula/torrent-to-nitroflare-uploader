import os
import re
import time
import logging
import requests
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

FILE_ID_PATTERN = re.compile(r"/view/([A-Z0-9]+)/")


class NitroflareUploader:
    def __init__(self, api_key: str, timeout: int = 3600, user: Optional[str] = None, premium_key: Optional[str] = None):
        self.api_key = api_key
        self.timeout = timeout
        self.user = user
        self.premium_key = premium_key
        self.base_url = "https://nitroflare.com/api/v2"
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
                try:
                    result = response.json()
                except ValueError:
                    logger.error("Upload response is not JSON: %s", response.text[:500])
                    return {
                        "status": "error",
                        "file": file_path.name,
                        "message": "Upload server returned non-JSON response",
                        "raw_response": response.text[:500],
                    }
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

    def _api_request(self, method: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Make a request to the Nitroflare General API v2."""
        url = f"{self.base_url}/{method}"
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("type") == "error":
                code = data.get("code")
                message = data.get("message", "Unknown API error")
                # Throttled / captcha required
                if code == 12 and self.user:
                    captcha_url = f"https://nitroflare.com/api/v2/solveCaptcha?user={self.user}"
                    logger.warning(f"Throttled by API. Please solve captcha: {captcha_url}")
                raise RuntimeError(f"Nitroflare API error {code}: {message}")

            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed for {method}: {e}")
            raise

    def get_file_info(self, file_ids: list) -> Dict[str, Any]:
        """
        Returns information about a file or multiple files.

        Args:
            file_ids: List of file IDs (e.g. ["3CB8F8AE25CF218"])

        Returns:
            Dict with file info under "result.files".
        """
        if not file_ids:
            raise ValueError("file_ids must not be empty")
        if len(file_ids) > 100:
            raise ValueError("Maximum 100 file IDs allowed per request")

        files_param = ",".join(file_ids)
        return self._api_request("getFileInfo", {"files": files_param})

    def get_download_link(self, file_id: str, **kwargs: str) -> Dict[str, Any]:
        """
        Returns a download link and other useful information.

        - Premium users: pass ``user`` and ``premiumKey`` (or set them on the
          uploader) to get a direct premium download link.
        - Free users:
            - Call with only ``file`` to get Step #1 data.
            - Call with ``file`` + ``startDownload`` + ``hash1`` + ``hash2``
              + ``captcha`` to get the final direct download link.

        Args:
            file_id: A file ID (e.g. "3CB8F8AE25CF218")
            **kwargs: Optional extra query params such as ``user``,
                ``premiumKey``, ``startDownload``, ``hash1``, ``hash2``,
                ``captcha``.

        Returns:
            Dict with download info under ``result``.
        """
        params: Dict[str, str] = {"file": file_id}
        if self.user and self.premium_key:
            params["user"] = self.user
            params["premiumKey"] = self.premium_key
        params.update(kwargs)
        return self._api_request("getDownloadLink", params)

    def get_free_download_step1(self, file_id: str) -> Dict[str, Any]:
        """Free download Step #1: generate a free download token."""
        return self.get_download_link(file_id)

    def get_free_download_step2(
        self,
        file_id: str,
        start_download: str,
        hash1: str,
        hash2: str,
        captcha: str,
    ) -> Dict[str, Any]:
        """Free download Step #2: return the final download link."""
        return self.get_download_link(
            file_id,
            startDownload=start_download,
            hash1=hash1,
            hash2=hash2,
            captcha=captcha,
        )

    def _extract_file_id(self, source: str) -> str:
        match = FILE_ID_PATTERN.search(source)
        if not match:
            raise ValueError(f"Could not extract Nitroflare file ID from: {source}")
        return match.group(1)

    def get_file_info_from_url(self, nitroflare_url: str) -> Dict[str, Any]:
        file_id = self._extract_file_id(nitroflare_url)
        return self.get_file_info([file_id])

    def get_final_download_link(self, nitroflare_url: str, **kwargs: str) -> Dict[str, Any]:
        """
        Convenience helper: given a Nitroflare file URL, return file info
        plus the final download link when possible.

        - Premium users get the direct download link immediately.
        - Free users:
            - Without extra kwargs, returns Step 1 data.
            - With ``startDownload``, ``hash1``, ``hash2``, ``captcha``,
              returns the final direct download link.
        """
        file_id = self._extract_file_id(nitroflare_url)
        info = self.get_file_info([file_id])
        link = self.get_download_link(file_id, **kwargs)
        return {
            "file_info": info,
            "download_link": link,
        }
