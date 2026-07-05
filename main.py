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
from src.direct_link_downloader import DirectLinkDownloader
from src.http_downloader import HTTPDownloader
from src.torrent_resolver import create_resolver
from src.nitroflare_uploader import NitroflareUploader
from src.gofile_uploader import GofileUploader
from src.utils import setup_logging, format_size, format_speed, format_time, zip_folder

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
    parser.add_argument(
        "--direct-link",
        help="Direct URL to download and upload (e.g., proxy tunnel link)",
    )
    parser.add_argument(
        "--http-only",
        action="store_true",
        help="Skip libtorrent — use HTTP(S) downloader + torrent resolver for all sources",
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


def create_gofile_uploader(config: ConfigLoader) -> GofileUploader:
    gofile_config = config.get_gofile_config()
    api_token = gofile_config.get("api_token")
    timeout = gofile_config.get("upload_timeout", 3600)
    region = gofile_config.get("region", "auto")

    return GofileUploader(api_token=api_token, timeout=timeout, region=region)


def ask_upload_destination(config: ConfigLoader) -> str:
    """Ask the user whether to upload to Gofile or Nitroflare.

    Returns one of: "gofile", "nitroflare", "both", "skip".
    Only offers backends that are actually configured.
    """
    nitroflare_config = config.get_nitroflare_config()
    gofile_config = config.get_gofile_config()

    nitroflare_key = nitroflare_config.get("api_key")
    has_nitroflare = bool(nitroflare_key) and nitroflare_key != "YOUR_NITROFLARE_USER_HASH_HERE"
    has_gofile = bool(gofile_config.get("api_token")) \
        and gofile_config.get("api_token") != "YOUR_GOFILE_API_TOKEN_HERE"

    options = []
    if has_gofile:
        options.append("gofile")
    if has_nitroflare:
        options.append("nitroflare")
    if has_gofile and has_nitroflare:
        options.append("both")
    options.append("skip")

    print("\nWhere would you like to upload the downloaded file(s)?")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")

    while True:
        try:
            choice = input(f"Enter choice (1-{len(options)}), default 1: ").strip()
            if choice == "":
                return options[0]
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
            print(f"Please enter a number between 1 and {len(options)}")
        except (ValueError, EOFError):
            print("Invalid input, please try again")


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
        source = data.get("source", "")
        name = data.get("name", torrent_id)

        # Build a label that shows the file/torrent name and, for magnet links,
        # the magnet link so the user can see what is being downloaded.
        if source.startswith("magnet:"):
            # Truncate the magnet link so the line stays readable
            magnet_short = source[:60] + ("..." if len(source) > 60 else "")
            label = f"{name} | {magnet_short}"
        elif source:
            label = f"{name} | {Path(source).name}"
        else:
            label = name

        print(
            f"\r[{label[:80]}] "
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


def direct_download_progress_callback(data: dict):
    downloaded = data.get("downloaded", 0)
    total = data.get("total", 0)
    progress = data.get("progress", 0)
    print(f"\rDirect download: {progress:.1f}% ({format_size(downloaded)}/{format_size(total)})", end="", flush=True)


def zip_progress_callback(data: dict):
    progress = data.get("progress", 0)
    print(f"\rZipping folder: {progress:.1f}%", end="", flush=True)


def process_direct_link(
    url: str,
    direct_downloader: DirectLinkDownloader,
    nitroflare_uploader: Optional[NitroflareUploader],
    gofile_uploader: Optional[GofileUploader],
    upload_destination: str = "nitroflare",
    upload: bool = True,
) -> bool:
    print(f"\n{'='*60}")
    print(f"Processing direct link: {url[:80]}...")
    print(f"{'='*60}")

    try:
        print("\nDownloading from direct link...")
        file_path = direct_downloader.download(url, progress_callback=direct_download_progress_callback)
        print(f"\nDownload complete: {file_path}")

        if not upload or upload_destination == "skip":
            return True

        files_to_upload = [str(file_path)]
        overall_success = True

        if upload_destination in ("nitroflare", "both") and nitroflare_uploader:
            print(f"\nUploading 1 file(s) to Nitroflare...")
            results = nitroflare_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Nitroflare", results) and overall_success

        if upload_destination in ("gofile", "both") and gofile_uploader:
            print(f"\nUploading 1 file(s) to Gofile...")
            results = gofile_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Gofile", results) and overall_success

        return overall_success

    except Exception as e:
        logger.error(f"Error processing direct link {url}: {e}", exc_info=True)
        print(f"\nError: {e}")
        return False


def process_single_torrent(
    torrent_source: str,
    downloader: TorrentDownloader,
    nitroflare_uploader: Optional[NitroflareUploader],
    gofile_uploader: Optional[GofileUploader],
    upload_destination: str = "nitroflare",
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

        if not upload or upload_destination == "skip":
            print("Skipping upload")
            return True

        files_to_upload = []

        if torrent_path.is_file():
            files_to_upload.append(str(torrent_path))
        elif torrent_path.is_dir():
            print(f"\nTorrent downloaded as folder. Zipping into single archive for upload...")
            zip_path = zip_folder(torrent_path, progress_callback=zip_progress_callback)
            print(f"\nCreated archive: {zip_path.name} ({format_size(zip_path.stat().st_size)})")
            files_to_upload.append(str(zip_path))

        if not files_to_upload:
            print("No files found to upload")
            return False

        overall_success = True

        if upload_destination in ("nitroflare", "both") and nitroflare_uploader:
            print(f"\nUploading {len(files_to_upload)} file(s) to Nitroflare...")
            results = nitroflare_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Nitroflare", results) and overall_success

        if upload_destination in ("gofile", "both") and gofile_uploader:
            print(f"\nUploading {len(files_to_upload)} file(s) to Gofile...")
            results = gofile_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Gofile", results) and overall_success

        return overall_success

    except Exception as e:
        logger.error(f"Error processing torrent {torrent_source}: {e}", exc_info=True)
        print(f"\nError: {e}")
        return False


def _print_upload_results(backend_name: str, results: list) -> bool:
    """Print upload results for a given backend. Returns True if all succeeded."""
    print(f"\n\n{'='*60}")
    print(f"{backend_name} Upload Results:")
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

    print(f"\n{backend_name} Summary: {success_count}/{len(results)} files uploaded successfully")
    return success_count == len(results)


def list_completed_torrents(downloader: Optional[TorrentDownloader]):
    print("\nCompleted Torrents:")
    print("-" * 60)

    if downloader is None:
        print("No libtorrent session (--http-only mode). No torrent history.")
        return

    if not downloader.completed_torrents and not downloader.active_torrents:
        print("No torrents in history")
        return

    for torrent_id, data in downloader.active_torrents.items():
        status = downloader.get_torrent_status(torrent_id)
        state = status.get("state", "unknown")
        progress = status.get("progress", 0)
        print(f"- {torrent_id[:50]}: {state} ({progress:.1f}%)")


# ------------------------------------------------------------------
# HTTP-only handler functions (no libtorrent)
# ------------------------------------------------------------------


def process_direct_link_http(
    url: str,
    http_downloader: HTTPDownloader,
    nitroflare_uploader: Optional[NitroflareUploader],
    gofile_uploader: Optional[GofileUploader],
    upload_destination: str = "nitroflare",
    upload: bool = True,
) -> bool:
    """Download a direct URL using HTTPDownloader, then upload."""
    print(f"\n{'='*60}")
    print(f"HTTP download: {url[:80]}...")
    print(f"{'='*60}")

    try:
        print("\nDownloading...")
        file_path = http_downloader.download(
            url, progress_callback=direct_download_progress_callback
        )
        print(f"\nDownload complete: {file_path}")

        if not upload or upload_destination == "skip":
            return True

        files_to_upload = [str(file_path)]
        overall_success = True

        if upload_destination in ("nitroflare", "both") and nitroflare_uploader:
            print(f"\nUploading 1 file(s) to Nitroflare...")
            results = nitroflare_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Nitroflare", results) and overall_success

        if upload_destination in ("gofile", "both") and gofile_uploader:
            print(f"\nUploading 1 file(s) to Gofile...")
            results = gofile_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Gofile", results) and overall_success

        return overall_success

    except Exception as e:
        logger.error(f"Error processing direct link {url}: {e}", exc_info=True)
        print(f"\nError: {e}")
        return False


def process_resolved_source(
    source: str,
    resolver,
    http_downloader: HTTPDownloader,
    nitroflare_uploader: Optional[NitroflareUploader],
    gofile_uploader: Optional[GofileUploader],
    upload_destination: str = "nitroflare",
    upload: bool = True,
) -> bool:
    """Resolve a magnet / torrent source via the resolver, then HTTP-download
    each resolved file and upload."""
    print(f"\n{'='*60}")
    print(f"Resolving: {source[:80]}...")
    print(f"{'='*60}")

    try:
        resolved = resolver.resolve(source)
        if not resolved:
            print(
                "\n⚠ No direct links were resolved.\n"
                "  Make sure 'torrent_resolver' is configured in config.yaml.\n"
                "  See config.example.yaml for details."
            )
            return False

        print(f"Resolved {len(resolved)} file(s). Downloading...")

        files_to_upload = []
        for item in resolved:
            url = item.get("url", "")
            filename = item.get("filename", None)
            if not url:
                continue
            file_path = http_downloader.download(
                url, filename=filename,
                progress_callback=direct_download_progress_callback,
            )
            print(f"\nDownload complete: {file_path}")
            files_to_upload.append(str(file_path))

        if not files_to_upload:
            print("No files downloaded.")
            return False

        if not upload or upload_destination == "skip":
            return True

        overall_success = True

        if upload_destination in ("nitroflare", "both") and nitroflare_uploader:
            print(f"\nUploading {len(files_to_upload)} file(s) to Nitroflare...")
            results = nitroflare_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Nitroflare", results) and overall_success

        if upload_destination in ("gofile", "both") and gofile_uploader:
            print(f"\nUploading {len(files_to_upload)} file(s) to Gofile...")
            results = gofile_uploader.upload_multiple(
                files_to_upload, progress_callback=upload_progress_callback
            )
            overall_success = _print_upload_results("Gofile", results) and overall_success

        return overall_success

    except Exception as e:
        logger.error(f"Error processing source {source}: {e}", exc_info=True)
        print(f"\nError: {e}")
        return False


def main():
    args = parse_arguments()

    log_config = {}
    if args.verbose:
        log_config["log_level"] = "DEBUG"

    config = load_config(args)
    logging_config = config.get_logging_config()
    log_config.setdefault("log_level", logging_config.get("level", "INFO"))
    log_config.setdefault("log_file", logging_config.get("file"))
    log_config.setdefault("max_size_mb", logging_config.get("max_size_mb", 10))
    log_config.setdefault("backup_count", logging_config.get("backup_count", 5))

    setup_logging(**log_config)

    logger.info("Starting Torrent to Nitroflare Uploader")

    # When --http-only is set, we never initialise libtorrent.
    http_only = args.http_only
    if not http_only:
        downloader = create_downloader(config)
    else:
        downloader = None

    # Create uploaders for each configured backend. A backend is only created
    # if its credentials are present in the config so the user can choose
    # between Gofile and Nitroflare (or both) at runtime.
    nitroflare_config = config.get_nitroflare_config()
    gofile_config = config.get_gofile_config()
    nitroflare_key = nitroflare_config.get("api_key")
    has_nitroflare = bool(nitroflare_key) and nitroflare_key != "YOUR_NITROFLARE_USER_HASH_HERE"
    has_gofile = bool(gofile_config.get("api_token")) \
        and gofile_config.get("api_token") != "YOUR_GOFILE_API_TOKEN_HERE"

    nitroflare_uploader = create_uploader(config) if has_nitroflare else None
    gofile_uploader = create_gofile_uploader(config) if has_gofile else None

    if args.list_completed:
        list_completed_torrents(downloader)
        return

    # ------------------------------------------------------------------
    # HTTP-only mode: process everything through HTTPDownloader +
    # TorrentResolver, no libtorrent involved.
    # ------------------------------------------------------------------
    if http_only or args.direct_link:
        torrent_config = config.get_torrent_config()
        download_dir = torrent_config.get("download_dir", "./downloads")

        http_cfg = config.get("http_downloader", {})
        http_downloader = HTTPDownloader(
            download_dir=download_dir,
            timeout=http_cfg.get("timeout", 3600),
            retry_attempts=http_cfg.get("retry_attempts", 3),
            retry_delay=http_cfg.get("retry_delay", 5.0),
        )

        resolver_cfg = config.get("torrent_resolver", {"backend": "none"})
        resolver = create_resolver(resolver_cfg)

        upload_destination = ask_upload_destination(config)

        if args.direct_link:
            success = process_direct_link_http(
                args.direct_link, http_downloader,
                nitroflare_uploader, gofile_uploader,
                upload_destination=upload_destination, upload=True,
            )
        elif http_only:
            # Determine the source from the other args
            source = None
            if args.magnet:
                source = args.magnet
            elif args.torrent_file:
                source = args.torrent_file
            elif args.torrent:
                source = args.torrent
            elif args.batch:
                print("Batch mode with --http-only is not yet supported. Use --direct-link instead.")
                sys.exit(1)
            else:
                print("No download source provided with --http-only.")
                sys.exit(1)

            success = process_resolved_source(
                source, resolver, http_downloader,
                nitroflare_uploader, gofile_uploader,
                upload_destination=upload_destination, upload=True,
            )
        else:
            success = False

        print(f"\n{'='*60}")
        print(f"HTTP processing: {'SUCCESS' if success else 'FAILED'}")
        print(f"{'='*60}")
        return

    if args.upload_only:
        print("Upload-only mode: uploading existing files from download directory")
        download_dir = Path(config.get_torrent_config().get("download_dir", "./downloads"))
        files = [str(f) for f in download_dir.rglob("*") if f.is_file()]
        if not files:
            print(f"No files found in {download_dir}")
            return
        upload_destination = ask_upload_destination(config)
        if upload_destination == "skip":
            print("Skipping upload")
            return
        print(f"Found {len(files)} files to upload")
        success_count = 0
        if upload_destination in ("nitroflare", "both") and nitroflare_uploader:
            results = nitroflare_uploader.upload_multiple(files, progress_callback=upload_progress_callback)
            if _print_upload_results("Nitroflare", results):
                success_count += 1
        if upload_destination in ("gofile", "both") and gofile_uploader:
            results = gofile_uploader.upload_multiple(files, progress_callback=upload_progress_callback)
            if _print_upload_results("Gofile", results):
                success_count += 1
        print(f"\nUploaded to {success_count} backend(s) successfully")
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

    # Ask the user where to upload the downloaded files. This is asked once
    # before the batch starts so the same destination applies to all torrents.
    upload_destination = ask_upload_destination(config)

    try:
        success_count = 0
        for torrent in torrents_to_process:
            if process_single_torrent(
                torrent, downloader,
                nitroflare_uploader, gofile_uploader,
                upload_destination=upload_destination, upload=True,
            ):
                success_count += 1

        print(f"\n{'='*60}")
        print(f"Batch complete: {success_count}/{len(torrents_to_process)} torrents processed successfully")
        print(f"{'='*60}")

    finally:
        downloader.stop()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
