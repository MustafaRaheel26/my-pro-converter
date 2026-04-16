#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install system dependencies
apt-get update && apt-get install -y poppler-utils

# Install Python dependencies
pip install -r requirements.txt