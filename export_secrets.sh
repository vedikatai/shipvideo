#!/bin/bash

# Script to export all environment secrets from .env file
# Usage: source export_secrets.sh
# Or: ./export_secrets.sh > exports.txt && source exports.txt

ENV_FILE="${1:-.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE" >&2
    echo "Usage: $0 [path_to_env_file]" >&2
    exit 1
fi

# Read .env file and export variables
# Ignores comments and empty lines
while IFS= read -r line || [ -n "$line" ]; do
    # Skip empty lines
    if [ -z "$line" ]; then
        continue
    fi
    
    # Skip comments
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
        continue
    fi
    
    # Skip lines that don't contain '='
    if [[ ! "$line" =~ = ]]; then
        continue
    fi
    
    # Extract key and value
    key=$(echo "$line" | cut -d '=' -f 1 | xargs)
    value=$(echo "$line" | cut -d '=' -f 2- | xargs)
    
    # Remove quotes if present
    value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    
    # Export the variable
    if [ -n "$key" ]; then
        export "$key=$value"
        echo "export $key=\"$value\""
    fi
done < "$ENV_FILE"

echo "# All secrets exported from $ENV_FILE" >&2
