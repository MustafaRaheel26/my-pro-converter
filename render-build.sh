#!/usr/bin/env bash
set -o errexit

# Update and install system dependencies for WeasyPrint
apt-get update
apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libffi-dev \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libxml2-dev \
    libxslt1-dev \
    poppler-utils \
    curl

# Upgrade pip
pip install --upgrade pip

# Install Python requirements
pip install -r requirements.txt
