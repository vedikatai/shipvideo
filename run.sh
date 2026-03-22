set -e
conda deactivate
source .venv/bin/activate
source export_secrets.sh

# Video: stepwise only by default (see VIDEO_PIPELINE in app/steps/pipeline.py).
# Capture: Agent Browser CLI by default (requires `agent-browser` on PATH).
# Use Playwright stepwise instead: export BROWSER_BACKEND=playwright
# Optional legacy script-first recording: export VIDEO_PIPELINE=script_first

uvicorn app.webhook:app --reload --port 8000
