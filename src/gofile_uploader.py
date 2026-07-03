import time
import logging
import requests
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class GofileUploader:
    """Uploader for the Gofile.io API.

    API docs: https://gofile.io/api

    Upload endpoint: POST https://upload.gofile.io/uploadfile
      - multipart/form-data: `file` (required), `folderId` (optional),
        `token` (optional, for authenticated uploads)
      - Response JSON: {"status": "ok", "data": {"downloadPage": "...", "code": "...", ...}}
    """

    def __init__(self, api_token: Optional[str] = None, timeout: int = 3600,
                 region: str = "auto"):
        self.api_token = api_token
        self.timeout = timeout
        self.base_url = "https://api.gofile.io"
        self.upload_endpoints = {
            "auto": "https://upload.gofile.io/uploadfile",
            "eu": "https://upload-eu-par.gofile.io/uploadfile",
            "na": "https://upload-na-phx.gofile.io/uploadfile",
            "sg": "https://upload-ap-sgp.gofile.io/uploadfile",
            "hk": "https://upload-ap-hkg.gofile.io/uploadfile",
            "jp": "https://upload-ap-tyo.gofile.io/uploadfile",
            "sa": "https://upload-sa-sao.gofile.io/uploadfile",
        }
        self.upload_url = self.upload_endpoints.get(region, self.upload_endpoints["auto"])

    def _get_account_id(self) -> Optional[str]:
        """Retrieve the account ID associated with the API token."""
        if not self.api_token:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/accounts/getid",
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "ok":
                account_id = result.get("data", {}).get("id")
                logger.info(f"Gofile account ID: {account_id}")
                return account_id
            logger.warning(f"Could not get Gofile account ID: {result}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to get Gofile account ID: {e}")
            return None

    def upload_file(self, file_path: str, progress_callback: Optional[Callable] = None,
                    folder_id: Optional[str] = None) -> dict:
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        logger.info(f"Uploading file to Gofile: {file_path.name} "
                    f"({file_path.stat().st_size / (1024*1024):.2f} MB)")

        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "application/octet-stream")}
                data = {}
                if self.api_token:
                    data["token"] = self.api_token
                if folder_id:
                    data["folderId"] = folder_id

                response = requests.post(
                    self.upload_url,
                    files=files,
                    data=data,
                    timeout=self.timeout,
                )

                response.raise_for_status()
                result = response.json()
                logger.info(f"Gofile upload response: {result}")

                if result.get("status") == "ok":
                    data_section = result.get("data", {})
                    download_page = data_section.get("downloadPage") or data_section.get("downloadUrl")
                    code = data_section.get("code")
                    # Build a download page URL if only a code is returned
                    if not download_page and code:
                        download_page = f"https://gofile.io/d/{code}"
                    return {
                        "status": "success",
                        "file": file_path.name,
                        "result": data_section,
                        "download_url": download_page,
                        "message": "Upload successful",
                    }

                error_msg = result.get("message", "Unknown error")
                logger.error(f"Gofile upload failed: {error_msg}")
                return {
                    "status": "error",
                    "file": file_path.name,
                    "message": error_msg,
                    "raw_response": result,
                }

        except requests.exceptions.Timeout:
            logger.error(f"Gofile upload timeout for {file_path.name}")
            return {
                "status": "timeout",
                "file": file_path.name,
                "message": f"Upload timed out after {self.timeout} seconds",
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Gofile upload failed for {file_path.name}: {e}")
            return {
                "status": "error",
                "file": file_path.name,
                "message": str(e),
            }

    def upload_multiple(self, file_paths: list, progress_callback: Optional[Callable] = None,
                        folder_id: Optional[str] = None) -> list:
        results = []
        total = len(file_paths)

        for idx, file_path in enumerate(file_paths, 1):
            logger.info(f"Uploading file {idx}/{total} to Gofile: {Path(file_path).name}")

            if progress_callback:
                progress_callback({
                    "current": idx,
                    "total": total,
                    "file": Path(file_path).name,
                    "status": "uploading",
                })

            result = self.upload_file(file_path, progress_callback=progress_callback,
                                      folder_id=folder_id)
            result["index"] = idx
            results.append(result)

            if idx < total:
                time.sleep(1)

        return results
