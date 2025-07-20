#!/bin/sh
# Generate config.yaml from template using environment variables
if [ ! -f config.yaml ]; then
    if [ -f config-template.yaml ]; then
        envsubst < config-template.yaml > config.yaml
    fi
fi
exec python main.py
