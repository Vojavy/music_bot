# 🎵 Music Bot

This repository contains a Telegram bot for downloading music. It supports running both as a standalone Python application and as a container in a Kubernetes (k3s) cluster via Docker and Kubernetes manifests.

## 🚀 Quick Start

```bash
python main.py
```

## 🎧 Features

* Download music directly to your server or PC.
* Supports Spotify (tracks, albums, playlists), YouTube, and direct file uploads.
* Automatically fetches metadata from:

  * Spotify
  * Last.fm
  * Discogs
  * MusicBrainz
* Kubernetes-ready setup with environment-based configuration.
* Docker image entrypoint with dynamic config generation.
* ⚠️⚠️⚠️⚠️⚠️ Project was partially coded while drunk vibe-coding — bugs may occur (e.g., infinite YouTube jams — too lazy to fix that ^^)⚠️⚠️⚠️⚠️⚠️.

## 🐋 Docker & Kubernetes

This project includes:

* `Dockerfile` – to build a containerized version of the bot.

* `start.sh` – entrypoint script used in the container. It substitutes environment variables into `config-template.yaml` to generate `config.yaml`, then launches the bot:

* `config-template.yaml` – a configuration template file. Used in Kubernetes to inject environment variables into config at runtime.
  *For usage example, see: [my self-hosted server project](https://github.com/Vojavy/shasse)*

## 🗂️ Project Structure

```
music_bot/
├── downloaders/                 # Source-specific downloaders
│   ├── __init__.py
│   ├── file.py                  # Direct file uploads
│   ├── spotify.py               # Spotify API and CLI integration
│   └── youtube.py               # YouTube support via spotDL or yt-dlp
│
├── .dockerignore
├── .gitignore
├── Dockerfile                   # Docker container definition
├── README.md                    # This file
├── config-template.yaml         # Template config for Docker/K8s
├── config.yaml                  # Runtime config (python main.py start)
├── config.py                    # Config loader using dataclasses
├── detector.py                  # Format/URL/content type detection
├── main.py                      # Telegram bot entrypoint
├── metadata.py                  # Metadata enrichment and ID3 tagging
├── requirements.txt             # Python dependencies
├── start.sh                     # Docker/K8s startup script
├── taglookup.py                 # External metadata search (Discogs, etc.)
└── utils.py                     # Utility functions
```