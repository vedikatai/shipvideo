#!/usr/bin/env python3
"""
Script to export all environment secrets from .env file
Usage:
    python export_secrets.py              # Prints export statements
    source <(python export_secrets.py)    # Export directly to current shell
    python export_secrets.py > exports.sh && source exports.sh
"""

import os
import sys
import re
from pathlib import Path


def parse_env_file(env_file_path):
    """Parse .env file and return dictionary of key-value pairs."""
    env_vars = {}
    
    if not os.path.exists(env_file_path):
        print(f"Error: .env file not found at {env_file_path}", file=sys.stderr)
        sys.exit(1)
    
    with open(env_file_path, 'r') as f:
        for line in f:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Skip comments
            if line.startswith('#'):
                continue
            
            # Parse KEY=VALUE
            if '=' in line:
                # Split on first '=' only
                parts = line.split('=', 1)
                key = parts[0].strip()
                value = parts[1].strip() if len(parts) > 1 else ''
                
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                
                if key:
                    env_vars[key] = value
    
    return env_vars


def export_to_shell(env_vars):
    """Print export statements for shell sourcing."""
    for key, value in env_vars.items():
        # Escape special characters in value
        escaped_value = value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')
        print(f'export {key}="{escaped_value}"')


def export_to_env(env_vars):
    """Actually set environment variables in current process."""
    for key, value in env_vars.items():
        os.environ[key] = value


def main():
    # Get .env file path from command line or use default
    env_file = sys.argv[1] if len(sys.argv) > 1 else '.env'
    env_file_path = Path(env_file)
    
    # Parse .env file
    env_vars = parse_env_file(env_file_path)
    
    if not env_vars:
        print(f"Warning: No environment variables found in {env_file_path}", file=sys.stderr)
        sys.exit(0)
    
    # Export to shell format
    export_to_shell(env_vars)
    
    # Also set in current Python process if running directly
    if sys.stdin.isatty():
        export_to_env(env_vars)
        print(f"\n# {len(env_vars)} environment variables exported from {env_file_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
