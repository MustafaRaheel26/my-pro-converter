#!/usr/bin/env bash
set -o errexit

# Install poppler for PDF to PPT
apt-get update && apt-get install -y poppler-utils

# Upgrade pip
pip install --upgrade pip

# Install requirements
pip install -r requirements.txt