#!/bin/bash
# Wrapper script to run pytest through pipenv for VS Code
# This ensures .env file is loaded automatically
cd "$(dirname "$0")/.."
exec pipenv run pytest "$@"
