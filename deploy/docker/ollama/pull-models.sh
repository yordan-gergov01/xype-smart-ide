#!/bin/sh
# Starts a private Ollama server on 127.0.0.1, pulls every model in
# OLLAMA_PULL_MODELS (space-separated) into the shared volume, then stops.
# Models already on the volume are skipped, so a start without internet works once
# they are present; set OLLAMA_REFRESH=1 to re-pull them. Cloud models (paid
# remote inference) are refused. Compose invokes this script through `sh`.
set -eu

: "${OLLAMA_PULL_MODELS:?OLLAMA_PULL_MODELS must be set}"

for model in $OLLAMA_PULL_MODELS; do
    case "$model" in
        *cloud*)
            echo "error: cloud model '$model' is not allowed (paid remote inference)" >&2
            exit 1
            ;;
    esac
done

ollama serve &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT

tries=0
until ollama list >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -gt 30 ]; then
        echo "error: ollama server did not start" >&2
        exit 1
    fi
    sleep 1
done

for model in $OLLAMA_PULL_MODELS; do
    if [ "${OLLAMA_REFRESH:-0}" != "1" ] && ollama show "$model" >/dev/null 2>&1; then
        echo "present: $model"
        continue
    fi
    echo "pulling $model"
    ollama pull "$model"
done
ollama list
