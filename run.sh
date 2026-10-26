#!/bin/bash

CONFIG_FILE="servers.json"

# Parse --config argument
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --config) CONFIG_FILE="$2"; shift ;;
    esac
    shift
done

# Terminate background server processes on exit (ex. if Ctrl+C)
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM

python3 server.py --config "$CONFIG_FILE" --id A &
python3 server.py --config "$CONFIG_FILE" --id B &
python3 server.py --config "$CONFIG_FILE" --id C &

# Wait for background processes
wait