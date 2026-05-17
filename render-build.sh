#!/usr/bin/env bash
set -o errexit

apt-get update && apt-get install -y poppler-utils

pip install --upgrade pip
pip install -r requirements.txt
