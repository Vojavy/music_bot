# Music Bot

This repository contains a Telegram music bot. The Dockerfile and Kubernetes manifests
allow running the bot in a k3s cluster.

## Building the Docker image

```bash
docker build -t music-bot:latest .
```

## Configuration in k3s

1. Create Kubernetes secrets with your API keys and tokens:

```bash
kubectl apply -f k8s/secret.yaml
```

2. Create the ConfigMap with the bot configuration template:

```bash
kubectl apply -f k8s/configmap.yaml
```

3. Deploy the bot using the provided deployment manifest (adjust the `image` field
   to match your registry):

```bash
kubectl apply -f k8s/deployment.yaml
```

The bot expects a persistent volume claim named `music-bot-pvc` for storing
downloads. A secret named `youtube-cookies` can be used to provide yt-dlp cookie
files if needed.
