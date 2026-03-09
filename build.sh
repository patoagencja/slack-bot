#!/usr/bin/env bash
set -e

pip install -r requirements.txt

# Pre-download modelu Whisper (żeby nie robić tego w trakcie obsługi requesta)
python -c "import whisper; whisper.load_model('${WHISPER_MODEL_SIZE:-tiny}')"
