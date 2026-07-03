import os
import logging
from pathlib import Path
from typing import Dict, Any
import yaml

logger = logging.getLogger(__name__)


class ConfigLoader:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if not self.config_path.exists():
            example_path = self.config_path.parent / "config.example.yaml"
            if example_path.exists():
                raise FileNotFoundError(
                    f"Config file not found: {self.config_path}. "
                    f"Please copy {example_path} to {self.config_path} and fill in your API key."
                )
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r") as f:
            self.config = yaml.safe_load(f) or {}

        self._validate()
        logger.info(f"Configuration loaded from {self.config_path}")

    def _validate(self):
        nitroflare_config = self.config.get("nitroflare", {})
        gofile_config = self.config.get("gofile", {})
        nitroflare_key = nitroflare_config.get("api_key")
        gofile_token = gofile_config.get("api_token")

        # At least one upload backend must be configured. We don't hard-fail
        # if Nitroflare is missing, because the user may choose Gofile instead.
        if (not nitroflare_key or nitroflare_key == "your_nitroflare_api_key_here") \
                and not gofile_token:
            raise ValueError(
                "No upload backend configured. Please set either "
                "'nitroflare.api_key' or 'gofile.api_token' in config.yaml"
            )

        torrent_config = self.config.get("torrent", {})
        if not torrent_config.get("download_dir"):
            self.config["torrent"]["download_dir"] = "./downloads"

        download_dir = Path(self.config["torrent"]["download_dir"])
        download_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        return value

    def get_nitroflare_config(self) -> Dict[str, Any]:
        return self.config.get("nitroflare", {})

    def get_gofile_config(self) -> Dict[str, Any]:
        return self.config.get("gofile", {})

    def get_torrent_config(self) -> Dict[str, Any]:
        return self.config.get("torrent", {})

    def get_logging_config(self) -> Dict[str, Any]:
        return self.config.get("logging", {})
