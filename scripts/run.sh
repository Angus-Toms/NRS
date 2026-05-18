#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
uvicorn app.main:app --reload --port 8001
