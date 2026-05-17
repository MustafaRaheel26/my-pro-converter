#!/usr/bin/env bash
set -o errexit

# Install system dependencies for WeasyPrint (and poppler for pdf2image)
apt-get update && apt-get install -y \
    poppler-utils \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libffi-dev \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libxml2-dev \
    libxslt1-dev

# Upgrade pip
pip install --upgrade pip

# Install Python requirements
pip install -r requirements.txt
