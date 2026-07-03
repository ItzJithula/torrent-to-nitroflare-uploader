# Torrent to Nitroflare Uploader

Downloads files from torrents and automatically uploads them to Nitroflare.

## Features

- Download torrents via magnet links, `.torrent` files, or HTTP URLs
- **Direct link downloads** — download any file from a direct URL and upload to Nitroflare
- **Folder uploads** — torrent directories are automatically zipped into a single archive before upload (Nitroflare has no folder concept)
- Automatic upload to Nitroflare after download completes
- Batch processing of multiple torrents
- Progress tracking and logging
- Configurable download settings (connections, ports, DHT, etc.)
- Upload-only mode for existing files
- **Google Colab support** — run it in the cloud via `colab_notebook.ipynb`
- Ubuntu/Linux optimized

## Requirements

- Python 3.8+
- libtorrent (Python bindings)
- Internet connection

## Installation on Ubuntu

### 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libtorrent-rasterbar-dev \
    build-essential libssl-dev libffi-dev
```

### 2. Clone or Copy the Project

```bash
git clone https://github.com/ItzJithula/torrent-to-nitroflare-uploader
cd torrent-to-nitroflare-uploader
```

### 3. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Edit `config.yaml` and add your Nitroflare API key:

```yaml
nitroflare:
  api_key: "your_actual_api_key_here"
```

## Usage

### Basic Usage

```bash
# Download a torrent file and upload to Nitroflare
python main.py movie.torrent

# Use a magnet link
python main.py --magnet "magnet:?xt=urn:btih:..."

# Download from a direct URL and upload to Nitroflare
python main.py --direct-link "https://example.com/file.zip"

# Batch process multiple torrents
python main.py --batch torrents.txt
```

### Batch File Format

Create a file `torrents.txt` with one torrent per line:

```
# My torrents
magnet:?xt=urn:btih:...
/path/to/file1.torrent
https://example.com/file2.torrent
```

### Command Line Options

```
python main.py --help

positional arguments:
  torrent               Torrent file path or magnet link

optional arguments:
  --magnet MAGNET       Magnet link to download
  --torrent-file FILE   Path to .torrent file
  --batch FILE          Path to text file with torrents (one per line)
  --config CONFIG       Path to config file (default: config.yaml)
  --api-key KEY         Nitroflare API key (overrides config)
  --download-dir DIR    Download directory (overrides config)
  --upload-only         Skip download, only upload existing files
  --list-completed      List completed torrents and exit
  --verbose, -v         Enable verbose logging
  --direct-link URL     Direct URL to download and upload
```

### Folder Uploads

When a torrent downloads as a folder (multiple files), the uploader automatically
zips the entire folder into a single `<folder_name>.zip` archive before uploading
to Nitroflare. This preserves the folder structure inside the archive, since
Nitroflare's API only supports single-file uploads.

### Direct Link Downloads

You can download any file from a direct URL (e.g., proxy tunnel links, CDN URLs)
and upload it to Nitroflare:

```bash
python main.py --direct-link "https://example.com/path/to/file.exe"
```

### Upload Only Mode

Upload existing files without downloading:

```bash
python main.py --upload-only
```

## Configuration

See `config.example.yaml` for all available options:

- **nitroflare.api_key**: Your Nitroflare API key (required)
- **nitroflare.upload_timeout**: Upload timeout in seconds (default: 3600)
- **torrent.download_dir**: Where to save downloaded files
- **torrent.max_active_downloads**: Max concurrent downloads
- **torrent.max_connections**: Max peer connections per torrent
- **torrent.listen_ports**: Ports for BitTorrent protocol
- **torrent.dht_enabled**: Enable DHT for peer discovery
- **logging.level**: Logging level (DEBUG, INFO, WARNING, ERROR)

## Getting a Nitroflare API Key

1. Log in to your Nitroflare account
2. Go to your account settings
3. Generate an API key
4. Add it to `config.yaml`

## Running on Google Colab

You can run this project on [Google Colab](https://colab.research.google.com)
for free cloud compute. Open `colab_notebook.ipynb` directly from this repo:

1. Go to [Google Colab](https://colab.research.google.com)
2. File → Open notebook → GitHub → paste repo URL:
   `https://github.com/ItzJithula/torrent-to-nitroflare-uploader`
3. Select `colab_notebook.ipynb`
4. Run the cells top-to-bottom, pasting your Nitroflare user hash and magnet/direct link where prompted

> ⚠️ **Note:** Torrenting on Colab may violate Google's ToS. Use only for legal
> content, and prefer the `--direct-link` mode for direct downloads which is
> less likely to be blocked.

## Running as a Service (Ubuntu)

### Create Systemd Service

Create `/etc/systemd/system/torrent-uploader.service`:

```ini
[Unit]
Description=Torrent to Nitroflare Uploader
After=network.target

[Service]
Type=simple
User=yourusername
WorkingDirectory=/path/to/torrent-nitroflare-uploader
ExecStart=/path/to/torrent-nitroflare-uploader/venv/bin/python main.py --batch torrents.txt
Restart=on-failure
RestartSec=60
Environment="PATH=/path/to/torrent-nitroflare-uploader/venv/bin:/usr/bin"

[Install]
WantedBy=multi-user.target
```

### Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable torrent-uploader
sudo systemctl start torrent-uploader
sudo systemctl status torrent-uploader
```

## Project Structure

```
torrent-nitroflare-uploader/
├── main.py                    # Main entry point
├── requirements.txt           # Python dependencies
├── config.example.yaml        # Example configuration
├── config.yaml                # Your configuration (create from example)
├── .gitignore
├── README.md
└── src/
    ├── __init__.py
    ├── torrent_downloader.py  # Torrent download logic
    ├── nitroflare_uploader.py # Nitroflare API upload logic
    ├── config_loader.py       # Configuration management
    └── utils.py               # Logging and utility functions
```

## Troubleshooting

### libtorrent installation issues

If `pip install libtorrent` fails, try:

```bash
sudo apt install python3-libtorrent
```

Or build from source:

```bash
sudo apt install libtorrent-rasterbar-dev
pip install python-libtorrent
```

### Port already in use

Change the listen ports in `config.yaml`:

```yaml
torrent:
  listen_ports:
    - 51413
    - 51414
```

### Upload fails

- Verify your Nitroflare API key is correct
- Check your internet connection
- Ensure the file size is within Nitroflare limits
- Check the log file for detailed error messages

## License

MIT
