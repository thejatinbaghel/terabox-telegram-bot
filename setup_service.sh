#!/bin/bash
set -e

echo "========================================================"
echo " Setting up TeraBox Telegram Bot 24/7 System Service"
echo "========================================================"

# 1. Stop and remove any docker containers to free memory and ports
if command -v docker &> /dev/null; then
    echo "Stopping Docker containers if running..."
    sudo docker compose down 2>/dev/null || true
fi

# 2. Install native system dependencies
echo "Installing Python, venv, and FFmpeg..."
sudo apt update
sudo apt install -y python3-pip python3-venv ffmpeg

# 3. Setup Python Virtual Environment
CURRENT_DIR=$(pwd)
echo "Working directory: $CURRENT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

echo "Installing Python requirements..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 4. Create systemd background service
echo "Configuring systemd 24/7 service..."
CURRENT_USER=$(whoami)

sudo tee /etc/systemd/system/terabox.service > /dev/null << EOF
[Unit]
Description=TeraBox Telegram Bot 24/7 Background Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
ExecStart=$CURRENT_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable and start service
echo "Reloading systemd and starting bot service..."
sudo systemctl daemon-reload
sudo systemctl enable terabox.service
sudo systemctl restart terabox.service

echo ""
echo "========================================================"
echo " Bot Service Started Successfully!"
echo " It is running 24/7 in the background independent of CMD."
echo "========================================================"
sudo systemctl status terabox.service --no-pager
