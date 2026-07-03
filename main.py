#!/usr/bin/env python3
"""
Torrent to Nitroflare Uploader

Downloads files from torrents and uploads them to Nitroflare.

Usage:
    python main.py <torrent_file_or_magnet_link>
    python main.py --magnet "magnet:?xt=urn:btih:..."
    python main.py --torrent-file "path/to/file.torrent"
    python main.py --batch "torrents.txt"  # one torrent per line

Environment variables (optional, override config.yaml):
    NITROFLARE_API_KEY - Your Nitroflare API key
    TORRENT_DOWNLOAD_DIR - Directory to save downloaded files
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from src.config_loader import ConfigLoader
from src.torrent_downloader import TorrentDownloader
from src.nitroflare_uploader import NitroflareUploader
from src.utils import setup_logging, format_size, format_speed, format_time

logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Download torrents and upload to Nitroflare",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py movie.torrent
  python main.py --magnet "magnet:?xt=urn:btih:..."
  python main.py --batch torrents.txt
  python main.py --torrent-file file.torrent --api-key YOUR_KEY
        """,
    )

    parser.add_argument(
        "torrent",
        nargs="?",
        help="Torrent file path or magnet link",
    )
    parser.add_argument(
        "--magnet",
        help="Magnet link to download",
    )
    parser.add_argument(
        "--torrent-file",
        help="Path to .torrent file",
    )
    parser.add_argument(
        "--batch",
        help="Path to text file with torrents (one per line)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--api-key",
        help="Nitroflare API key (overrides config)",
    )
    parser.add_argument(
        "--download-dir",
        help="Download directory (overrides config)",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="Skip download, only upload existing files in download dir",
    )
    parser.add_argument(
        "--list-completed",
        action="store_true",
        help="List completed torrents and exit",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def load_config(args) -> ConfigLoader:
    try:
        config = ConfigLoader(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nPlease create a config.yaml file. You can copy config.example.yaml as a starting point.")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.api_key:
        config.config.setdefault("nitroflare", {})["api_key"] = args.api_key

    if args.download_dir:
        config.config.setdefault("torrent", {})["download_dir"] = args.download_dir

    return config


def create_downloader(config: ConfigLoader) -> TorrentDownloader:
    torrent_config = config.get_torrent_config()

    return TorrentDownloader(
        download_dir=torrent_config.get("download_dir", "./downloads"),
        max_active_downloads=torrent_config.get("max_active_downloads", 3),
        max_connections=torrent_config.get("max_connections", 50),
        max_uploads_per_torrent=torrent_config.get("max_uploads_per_torrent", 10),
        upload_rate_limit=torrent_config.get("upload_rate_limit", 0),
        download_rate_limit=torrent_config.get("download_rate_limit", 0),
        listen_ports=torrent_config.get("listen_ports", [6881, 6882, 6883]),
        dht_enabled=torrent_config.get("dht_enabled", True),
        lsd_enabled=torrent_config.get("lsd_enabled", True),
        upnp_enabled=torrent_config.get("upnp_enabled", True),
        natpmp_enabled=torrent_config.get("natpmp_enabled", True),
    )


def create_uploader(config: ConfigLoader) -> NitroflareUploader:
    nitroflare_config = config.get_nitroflare_config()
    api_key = nitroflare_config.get("api_key")
    timeout = nitroflare_config.get("upload_timeout", 3600)

    return NitroflareUploader(api_key=api_key, timeout=timeout)


def progress_callback_factory(torrent_id: str):
    last_update = [0.0]

    def callback(data):
        current_time = time.time()
        if current_time - last_update[0] < 1.0:
            return
        last_update[0] = current_time

        progress = data.get("progress", 0)
        download_rate = data.get("download_rate", 0)
        state = data.get("state", "unknown")

        print(
            f"\r[{torrent_id[:30]}] "
            f"Progress: {progress:.1f}% | "
            f"Speed: {format_speed(download_rate * 1024)} | "
            f"State: {state}",
            end="",
            flush=True,
        )

    return callback


def upload_progress_callback(data: dict):
    current = data.get("current", 0)
    total = data.get("total", 0)
    file_name = data.get("file", "unknown")
    status = data.get("status", "uploading")

    if status == "uploading":
        print(f"\rUploading {current}/{total}: {file_name}", end="", flush=True)
    else:
        print(f"\rProcessing {current}/{total}: {file_name}", end="", flush=True)


def process_single_torrent(
    torrent_source: str,
    downloader: TorrentDownloader,
    uploader: NitroflareUploader,
    upload: bool = True,
) -> bool:
    print(f"\n{'='*60}")
    print(f"Processing: {torrent_source}")
    print(f"{'='*60}")

    torrent_id = None
    try:
        progress_callback = progress_callback_factory(torrent_source)
        torrent_id = downloader.add_torrent(torrent_source, progress_callback=progress_callback)

        print(f"\nDownloading: {torrent_id}")
        torrent_path = downloader.wait_for_completion(torrent_id)

        print(f"\nDownload complete: {torrent_path}")

        if not upload:
            print("Skipping upload (--upload-only not set)")
            return True

        files_to_upload = []

        if torrent_path.is_file():
            files_to_upload.append(str(torrent_path))
        elif torrent_path.is_dir():
            files_to_upload = [
                str(f) for f in torrent_path.rglob("*") if f.is_file()
            ]
            print(f"Found {len(files_to_upload)} files in directory")

        if not files_to_upload:
            print("No files found to upload")
            return False

        print(f"\nUploading {len(files_to_upload)} file(s) to Nitroflare...")
        results = uploader.upload_multiple(files_to_upload, progress_callback=upload_progress_callback)

        print(f"\n\n{'='*60}")
        print("Upload Results:")
        print(f"{'='*60}")

        success_count = 0
        for result in results:
            status_icon = "✓" if result.get("status") == "success" else "✗"
            file_name = result.get("file", "unknown")
            message = result.get("message", "")

            print(f"{status_icon} {file_name}: {message}")

            if result.get("status") == "success":
                success_count += 1
                download_url = result.get("download_url")
                if download_url:
                    print(f"  URL: {download_url}")

        print(f"\nSummary: {success_count}/{len(results)} files uploaded successfully")
        return success_count == len(files_to_upload)

    except Exception as e:
        logger.error(f"Error processing torrent {torrent_source}: {e}", exc_info=True)
        print(f"\nError: {e}")
        return False


def list_completed_torrents(downloader: TorrentDownloader):
    print("\nCompleted Torrents:")
    print("-" * 60)

    if not downloader.completed_torrents and not downloader.active_torrents:
        print("No torrents in history")
        return

    for torrent_id, data in downloader.active_torrents.items():
        status = downloader.get_torrent_status(torrent_id)
        state = status.get("state", "unknown")
        progress = status.get("progress", 0)
        print(f"- {torrent_id[:50]}: {state} ({progress:.1f}%)")


def main():
    args = parse_arguments()

    log_config = {}
    if args.verbose:
        log_config["level"] = "DEBUG"

    config = load_config(args)
    logging_config = config.get_logging_config()
    log_config.setdefault("level", logging_config.get("level", "INFO"))
    log_config.setdefault("file", logging_config.get("file"))
    log_config.setdefault("max_size_mb", logging_config.get("max_size_mb", 10))
    log_config.setdefault("backup_count", logging_config.get("backup_count", 5))

    setup_logging(**log_config)

    logger.info("Starting Torrent to Nitroflare Uploader")

    downloader = create_downloader(config)
    uploader = create_uploader(config)

    if args.list_completed:
        list_completed_torrents(downloader)
        return

    if args.upload_only:
        print("Upload-only mode: uploading existing files from download directory")
        download_dir = Path(config.get_torrent_config().get("download_dir", "./downloads"))
        files = [str(f) for f in download_dir.rglob("*") if f.is_file()]
        if not files:
            print(f"No files found in {download_dir}")
            return
        print(f"Found {len(files)} files to upload")
        results = uploader.upload_multiple(files, progress_callback=upload_progress_callback)
        success = sum(1 for r in results if r.get("status") == "success")
        print(f"\nUploaded {success}/{len(files)} files successfully")
        return

    torrents_to_process = []

    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            print(f"Batch file not found: {args.batch}")
            sys.exit(1)
        with open(batch_file, "r") as f:
            torrents_to_process = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        print(f"Loaded {len(torrents_to_process)} torrents from {args.batch}")
    elif args.magnet:
        torrents_to_process.append(args.magnet)
    elif args.torrent_file:
        torrents_to_process.append(args.torrent_file)
    elif args.torrent:
        torrents_to_process.append(args.torrent)
    else:
        parser = parse_arguments()
        parser.print_help()
        sys.exit(1)

    downloader.start()

    try:
        success_count = 0
        for torrent in torrents_to_process:
            if process_single_torrent(torrent, downloader, uploader, upload=True):
                success_count += 1

        print(f"\n{'='*60}")
        print(f"Batch complete: {success_count}/{len(torrents_to_process)} torrents processed successfully")
        print(f"{'='*60}")

    finally:
        downloader.stop()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
