#!/usr/bin/env bash
# Starts Maid Me on Linux. Arguments go straight to the app (e.g. --texto, --console, --mics).
cd "$(dirname "$(readlink -f "$0")")"
# the CUDA libraries come from pip wheels: point the loader at them so Whisper can use the GPU
for d in .venv/lib/python3*/site-packages/nvidia/*/lib; do
    [ -d "$d" ] && LD_LIBRARY_PATH="$PWD/$d:$LD_LIBRARY_PATH"
done
export LD_LIBRARY_PATH
exec .venv/bin/python -m assistente "$@"
