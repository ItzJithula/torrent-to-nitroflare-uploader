import os
import time
import logging
from pathlib import Path
from typing import Optional, Callable
import libtorrent as lt

logger = logging.getLogger(__name__)


class TorrentDownloader:
    def __init__(self, download_dir: str, max_active_downloads: int = 3,
                 max_connections: int = 50, max_uploads_per_torrent: int = 10,
                 upload_rate_limit: int = 0, download_rate_limit: int = 0,
                 listen_ports: list = None, dht_enabled: bool = True,
                 lsd_enabled: bool = True, upnp_enabled: bool = True,
                 natpmp_enabled: bool = True):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_active_downloads = max_active_downloads
        self.max_connections = max_connections
        self.max_uploads_per_torrent = max_uploads_per_torrent
        self.upload_rate_limit = upload_rate_limit
        self.download_rate_limit = download_rate_limit
        self.listen_ports = listen_ports or [6881, 6882, 6883]
        self.dht_enabled = dht_enabled
        self.lsd_enabled = lsd_enabled
        self.upnp_enabled = upnp_enabled
        self.natpmp_enabled = natpmp_enabled

        self.session: Optional[lt.session] = None
        self.active_torrents: dict = {}
        self.completed_torrents: list = []

    def _setup_session(self) -> lt.session:
        settings = lt.session_params()
        settings.settings["user_agent"] = "torrent-nitroflare-uploader/1.0"
        settings.settings["choking_algorithm"] = lt.choking_algorithm_t.rate_based_choker
        settings.settings["seed_choking_algorithm"] = lt.seed_choking_algorithm_t.fastest_upload
        settings.settings["max_connections"] = self.max_connections
        settings.settings["max_uploads_per_torrent"] = self.max_uploads_per_torrent
        settings.settings["upload_rate_limit"] = self.upload_rate_limit
        settings.settings["download_rate_limit"] = self.download_rate_limit
        settings.settings["active_downloads"] = self.max_active_downloads
        settings.settings["active_seeds"] = 1
        settings.settings["active_limit"] = self.max_active_downloads + 1

        session = lt.session(settings)

        if self.listen_ports:
            session.listen_on(self.listen_ports[0], self.listen_ports[-1])

        if self.dht_enabled:
            session.start_dht()
            dht_settings = lt.dht_settings()
            dht_settings.max_peers = 200
            session.set_dht_settings(dht_settings)

        if self.lsd_enabled:
            session.start_lsd()

        if self.upnp_enabled:
            session.start_upnp()

        if self.natpmp_enabled:
            session.start_natpmp()

        return session

    def start(self):
        if self.session is None:
            self.session = self._setup_session()
            logger.info("Torrent session started")

    def stop(self):
        if self.session:
            self.session.pause()
            logger.info("Torrent session stopped")

    def add_torrent(self, torrent_source: str, save_path: Optional[str] = None,
                    progress_callback: Optional[Callable] = None) -> str:
        if self.session is None:
            self.start()

        save_path = Path(save_path) if save_path else self.download_dir
        save_path.mkdir(parents=True, exist_ok=True)

        params = lt.add_torrent_params()
        params.save_path = str(save_path)
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        torrent_handle = None
        torrent_info = None

        if torrent_source.startswith("magnet:"):
            params.url = torrent_source
            torrent_handle = self.session.add_torrent(params)
            logger.info(f"Added magnet link: {torrent_source[:80]}...")
        elif torrent_source.startswith("http://") or torrent_source.startswith("https://"):
            params.url = torrent_source
            torrent_handle = self.session.add_torrent(params)
            logger.info(f"Added HTTP torrent: {torrent_source}")
        else:
            torrent_path = Path(torrent_source)
            if not torrent_path.exists():
                raise FileNotFoundError(f"Torrent file not found: {torrent_source}")

            ti = lt.torrent_info(str(torrent_path))
            params.ti = ti
            torrent_handle = self.session.add_torrent(params)
            torrent_info = ti
            logger.info(f"Added torrent file: {torrent_path.name}")

        torrent_name = "Unknown"
        if torrent_info:
            torrent_name = torrent_info.name()
        else:
            time.sleep(2)
            if torrent_handle and torrent_handle.has_metadata():
                torrent_info = torrent_handle.get_torrent_info()
                if torrent_info:
                    torrent_name = torrent_info.name()

        torrent_id = torrent_name
        self.active_torrents[torrent_id] = {
            "handle": torrent_handle,
            "torrent_info": torrent_info,
            "save_path": str(save_path),
            "progress_callback": progress_callback,
            "source": torrent_source,
        }

        return torrent_id

    def get_torrent_status(self, torrent_id: str) -> dict:
        if torrent_id not in self.active_torrents:
            return {"error": "Torrent not found"}

        handle = self.active_torrents[torrent_id]["handle"]
        status = handle.status()

        state_map = {
            lt.torrent_status.states.queued_for_checking: "queued_for_checking",
            lt.torrent_status.states.checking_files: "checking_files",
            lt.torrent_status.states.downloading_metadata: "downloading_metadata",
            lt.torrent_status.states.downloading: "downloading",
            lt.torrent_status.states.finished: "finished",
            lt.torrent_status.states.seeding: "seeding",
            lt.torrent_status.states.allocating: "allocating",
            lt.torrent_status.states.checking_resume_data: "checking_resume_data",
        }

        state_str = state_map.get(status.state, f"unknown({status.state})")

        return {
            "torrent_id": torrent_id,
            "state": state_str,
            "progress": status.progress * 100,
            "download_rate": status.download_rate / 1024,
            "upload_rate": status.upload_rate / 1024,
            "num_peers": status.num_peers,
            "total_wanted": status.total_wanted,
            "total_wanted_done": status.total_wanted_done,
            "eta": status.eta,
            "save_path": self.active_torrents[torrent_id]["save_path"],
        }

    def wait_for_completion(self, torrent_id: str, check_interval: float = 2.0) -> Path:
        if torrent_id not in self.active_torrents:
            raise ValueError(f"Torrent not found: {torrent_id}")

        torrent_data = self.active_torrents[torrent_id]
        handle = torrent_data["handle"]
        save_path = Path(torrent_data["save_path"])
        progress_callback = torrent_data.get("progress_callback")
        torrent_source = torrent_data.get("source", "")

        logger.info(f"Waiting for torrent to complete: {torrent_id}")

        while True:
            status = handle.status()

            if progress_callback:
                progress_callback({
                    "torrent_id": torrent_id,
                    "progress": status.progress * 100,
                    "download_rate": status.download_rate / 1024,
                    "state": str(status.state),
                    "source": torrent_source,
                    "name": torrent_id,
                })

            if status.is_seeding or status.progress >= 1.0:
                logger.info(f"Torrent completed: {torrent_id}")
                break

            if status.state == lt.torrent_status.states.finished:
                logger.info(f"Torrent finished downloading: {torrent_id}")
                break

            time.sleep(check_interval)

        torrent_info = handle.get_torrent_info()
        if torrent_info:
            torrent_name = torrent_info.name()
            torrent_path = save_path / torrent_name
            if torrent_path.exists():
                self.completed_torrents.append(torrent_id)
                return torrent_path

        return save_path

    def get_completed_files(self, torrent_id: str) -> list:
        if torrent_id not in self.active_torrents:
            return []

        handle = self.active_torrents[torrent_id]["handle"]
        torrent_info = handle.get_torrent_info()
        if not torrent_info:
            return []

        save_path = Path(self.active_torrents[torrent_id]["save_path"])
        files = []

        for i in range(torrent_info.num_files()):
            file_entry = torrent_info.file_at(i)
            file_path = save_path / file_entry.path
            if file_path.exists():
                files.append(str(file_path))

        return files

    def remove_torrent(self, torrent_id: str, delete_files: bool = False):
        if torrent_id not in self.active_torrents:
            return

        handle = self.active_torrents[torrent_id]["handle"]
        self.session.remove_torrent(handle, delete_files)
        del self.active_torrents[torrent_id]
        logger.info(f"Removed torrent: {torrent_id}")
