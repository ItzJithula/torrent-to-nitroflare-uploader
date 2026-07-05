#!/usr/bin/env python3
"""
Standalone download + upload script for Google Colab.

Zero libtorrent dependency — uses only ``requests`` and other pure-Python
libraries that are available on Colab out of the box or easily pip-installed.

Usage on Colab
--------------
    1. Upload this file (or clone the repo).
    2. Create a ``config.yaml`` with at least one upload backend configured.
    3. Run::

        !python colab_download.py --direct-link "https://example.com/file.zip"
        !python colab_download.py --magnet "magnet:?xt=urn:btih:..."

For magnets / .torrent files you need to configure a ``torrent_resolver``
backend in ``config.yaml`` (see ``config.example.yaml``).
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Local imports – note: NO libtorrent anywhere
from src.http_downloader import HTTPDownloader
from src.torrent_resolver import create_resolver
from src.config_loader import ConfigLoader
from src.nitroflare_uploader import NitroflareUploader
from src.gofile_uploader import GofileUploader
from src.utils import setup_logging, format_size

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download (via HTTP) and upload — no libtorrent required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--magnet",
        help="Magnet link to resolve and download",
    )
    parser.add_argument(
        "--torrent-file",
        help="Path or URL to a .torrent file",
    )
    parser.add_argument(
        "--direct-link",
        help="Direct HTTP(S) URL to download",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--download-dir",
        help="Override download directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )
    return parser.parse_args()


def progress_callback(data: dict):
    downloaded = data.get("downloaded", 0)
    total = data.get("total", 0)
    progress = data.get("progress", 0)
    if total > 0:
        print(
            f"\rDownloading: {progress:.1f}% ({format_size(downloaded)}/{format_size(total)})    ",
            end="", flush=True,
        )
    else:
        print(
            f"\rDownloaded: {format_size(downloaded)}    ",
            end="", flush=True,
        )


def upload_progress_callback(data: dict):
    current = data.get("current", 0)
    total = data.get("total", 0)
    file_name = data.get("file", "unknown")
    print(f"\rUploading {current}/{total}: {file_name}    ", end="", flush=True)


def main():
    args = parse_args()

    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(log_level=log_level)

    # Load config
    config = ConfigLoader(args.config)

    download_dir = args.download_dir or config.get_torrent_config().get(
        "download_dir", "./downloads"
    )

    # Determine download source
    source = None
    source_type = None
    if args.direct_link:
        source = args.direct_link
        source_type = "direct"
    elif args.magnet:
        source = args.magnet
        source_type = "magnet"
    elif args.torrent_file:
        source = args.torrent_file
        source_type = "torrent"
    else:
        print("No download source provided. Use --direct-link, --magnet, or --torrent-file.")
        sys.exit(1)

    # --- Download phase ---
    http_downloader_config = config.get("http_downloader", {})
    downloader = HTTPDownloader(
        download_dir=download_dir,
        timeout=http_downloader_config.get("timeout", 3600),
        retry_attempts=http_downloader_config.get("retry_attempts", 3),
        retry_delay=http_downloader_config.get("retry_delay", 5.0),
    )

    files_to_upload = []

    if source_type == "direct":
        print(f"\n{'='*60}")
        print(f"Downloading direct link: {source[:80]}...")
        print(f"{'='*60}")
        filepath = downloader.download(
            source, progress_callback=progress_callback
        )
        print(f"\n✓ Downloaded: {filepath}")
        files_to_upload.append(str(filepath))

    else:
        # magnet / torrent – resolve via configured external service
        resolver_config = config.get("torrent_resolver", {"backend": "none"})
        resolver = create_resolver(resolver_config)

        print(f"\n{'='*60}")
        print(f"Resolving: {source[:80]}...")
        print(f"{'='*60}")

        resolved = resolver.resolve(source)

        if not resolved:
            print(
                "\n⚠ No direct links resolved.\n"
                "  Make sure 'torrent_resolver' is configured in config.yaml.\n"
                "  See config.example.yaml for details."
            )
            sys.exit(1)

        print(f"Resolved {len(resolved)} file(s). Starting download(s)...\n")

        for item in resolved:
            url = item.get("url", "")
            filename = item.get("filename", None)
            if not url:
                continue
            filepath = downloader.download(
                url, filename=filename, progress_callback=progress_callback
            )
            print(f"\n✓ Downloaded: {filepath}")
            files_to_upload.append(str(filepath))

    if not files_to_upload:
        print("No files downloaded.")
        sys.exit(1)

    # --- Upload phase ---
    nitroflare_config = config.get_nitroflare_config()
    gofile_config = config.get_gofile_config()
    has_nitroflare = bool(nitroflare_config.get("api_key")) and \
        nitroflare_config["api_key"] != "YOUR_NITROFLARE_USER_HASH_HERE"
    has_gofile = bool(gofile_config.get("api_token")) and \
        gofile_config.get("api_token") != "YOUR_GOFILE_API_TOKEN_HERE"

    if not has_nitroflare and not has_gofile:
        print("\nNo upload backends configured. Files saved to:", download_dir)
        return

    # Ask user where to upload
    print("\nWhere would you like to upload?")
    options = []
    if has_gofile:
        options.append("gofile")
    if has_nitroflare:
        options.append("nitroflare")
    if has_gofile and has_nitroflare:
        options.append("both")
    options.append("skip")

    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    choice = input(f"Enter choice (1-{len(options)}), default 1: ").strip()
    if choice == "":
        dest = options[0]
    else:
        try:
            dest = options[int(choice) - 1]
        except (ValueError, IndexError):
            dest = options[0]

    if dest == "skip":
        print(f"\nFiles saved to {download_dir}")
        return

    overall_success = True

    if dest in ("nitroflare", "both") and has_nitroflare:
        uploader = NitroflareUploader(
            api_key=nitroflare_config["api_key"],
            timeout=nitroflare_config.get("upload_timeout", 3600),
        )
        print(f"\nUploading {len(files_to_upload)} file(s) to Nitroflare...")
        results = uploader.upload_multiple(
            files_to_upload, progress_callback=upload_progress_callback
        )
        print()
        for r in results:
            status = "✓" if r.get("status") == "success" else "✗"
            print(f"  {status} {r.get('file', '?')}: {r.get('message', '')}")
            if r.get("download_url"):
                print(f"     URL: {r['download_url']}")

    if dest in ("gofile", "both") and has_gofile:
        uploader = GofileUploader(
            api_token=gofile_config["api_token"],
            timeout=gofile_config.get("upload_timeout", 3600),
            region=gofile_config.get("region", "auto"),
        )
        print(f"\nUploading {len(files_to_upload)} file(s) to Gofile...")
        results = uploader.upload_multiple(
            files_to_upload, progress_callback=upload_progress_callback
        )
        print()
        for r in results:
            status = "✓" if r.get("status") == "success" else "✗"
            print(f"  {status} {r.get('file', '?')}: {r.get('message', '')}")
            if r.get("download_url"):
                print(f"     URL: {r['download_url']}")

    print(f"\n{'='*60}")
    print("All done!" if overall_success else "Some uploads failed.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
