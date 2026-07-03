#!/bin/bash
# Ubuntu Setup Script for Torrent to Nitroflare Uploader
# Run this on your Ubuntu machine

set -e

echo "=========================================="
echo "Torrent to Nitroflare Uploader Setup"
echo "=========================================="
echo ""

# Check if running on Ubuntu/Debian
if ! [ -f /etc/debian_version ]; then
    echo "Warning: This script is designed for Ubuntu/Debian systems"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "[1/6] Updating package lists..."
sudo apt update

echo ""
echo "[2/6] Installing system dependencies..."
sudo apt install -y python3 python3-pip python3-venv \
    libtorrent-rasterbar-dev \
    build-essential libssl-dev libffi-dev \
    git curl wget

echo ""
echo "[3/6] Creating project directory..."
PROJECT_DIR="$HOME/torrent-nitroflare-uploader"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

echo ""
echo "[4/6] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo ""
echo "[5/6] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "[6/6] Setting up configuration..."
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo ""
    echo "Created config.yaml from example."
    echo ""
    echo "IMPORTANT: Please edit config.yaml and add your Nitroflare API key:"
    echo "  nano config.yaml"
    echo ""
    read -p "Press Enter to open config.yaml in nano..."
    nano config.yaml
else
    echo "config.yaml already exists, skipping."
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Project directory: $PROJECT_DIR"
echo ""
echo "To activate the virtual environment:"
echo "  cd $PROJECT_DIR"
echo "  source venv/bin/activate"
echo ""
echo "To run the uploader:"
echo "  python main.py --help"
echo ""
echo "Example usage:"
echo "  python main.py movie.torrent"
echo "  python main.py --magnet 'magnet:?xt=urn:btih:...'"
echo "  python main.py --batch torrents.txt"
echo ""
