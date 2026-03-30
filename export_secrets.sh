#!/bin/bash

ENV_FILE="${1:-.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE" >&2
    echo "Usage: $0 [path_to_env_file]" >&2
    exit 1
fi

while IFS= read -r line || [ -n "$line" ]; do
    if [ -z "$line" ]; then
        continue
    fi
    
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
        continue
    fi
    
    if [[ ! "$line" =~ = ]]; then
        continue
    fi
    
    key=$(echo "$line" | cut -d '=' -f 1 | xargs)
    value=$(echo "$line" | cut -d '=' -f 2- | xargs)
    
    value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    
    if [ -n "$key" ]; then
        export "$key=$value"
        echo "export $key=\"$value\""
    fi
done < "$ENV_FILE"

echo "# All secrets exported from $ENV_FILE" >&2
