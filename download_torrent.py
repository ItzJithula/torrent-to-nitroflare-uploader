#!/usr/bin/env python3
"""Simple script to download a torrent file using libtorrent."""

import libtorrent as lt
import time
from pathlib import Path


def download_torrent(torrent_path: str, save_dir: str = "./downloads"):
    """Download a torrent file to the specified directory."""
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    # Create session
    settings = lt.session_params()
    session = lt.session(settings)

    # Load torrent
    torrent_path = Path(torrent_path)
    if not torrent_path.exists():
        print(f"Error: Torrent file not found: {torrent_path}")
        return False

    ti = lt.torrent_info(str(torrent_path))
    params = {
        "save_path": str(save_path),
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        "ti": ti,
    }

    handle = session.add_torrent(params)
    print(f"Added torrent: {ti.name()}")
    print(f"Save path: {save_path}")
    print("Downloading...")

    # Wait for completion
    while True:
        status = handle.status()
        progress = status.progress * 100
        state = status.state

        print(f"\rProgress: {progress:.1f}% | State: {state}", end="", flush=True)

        if status.is_seeding or status.progress >= 1.0:
            print("\nDownload complete!")
            break

        time.sleep(1)

    # Get the downloaded file path
    torrent_name = ti.name()
    torrent_path = save_path / torrent_name

    if torrent_path.exists():
        print(f"File saved to: {torrent_path}")
        return True
    else:
        print(f"File not found at expected path: {torrent_path}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python download_torrent.py <torrent_file> [save_dir]")
        sys.exit(1)

    torrent_file = sys.argv[1]
    save_dir = sys.argv[2] if len(sys.argv) > 2 else "./downloads"

    success = download_torrent(torrent_file, save_dir)
    sys.exit(0 if success else 1)
