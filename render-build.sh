#!/usr/bin/env bash
set -o errexit

# Update package list and install LibreOffice headless
apt-get update
apt-get install -y --no-install-recommends \
    libreoffice-headless \
    libreoffice-writer \
    libreoffice-common \
    poppler-utils \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libffi-dev \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libxml2-dev \
    libxslt1-dev

# Verify installation
if ! command -v libreoffice &> /dev/null; then
    echo "ERROR: LibreOffice not installed successfully"
    exit 1
fi

# Upgrade pip and install Python requirements
pip install --upgrade pip
pip install -r requirements.txt
