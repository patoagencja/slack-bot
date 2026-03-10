"""Transkrypcja wiadomości głosowych ze Slacka przez OpenAI Whisper API."""
import logging
import os
import tempfile

import requests
from openai import OpenAI

import _ctx

logger = logging.getLogger(__name__)

# MIME types Slackowych głosówek (webm, mp4, ogg, mpeg, m4a, wav)
SLACK_AUDIO_MIMES = {
    "audio/webm",
    "audio/mp4",
    "audio/ogg",
    "audio/mpeg",
    "audio/m4a",
    "audio/wav",
    "audio/flac",
    "audio/aac",
    "audio/x-m4a",
    "video/mp4",   # Slack native voice clips
    "video/webm",  # Slack native voice clips (niektóre platformy)
}

# Mapowanie mime → rozszerzenie pliku
_MIME_TO_EXT = {
    "audio/webm":  "webm",
    "audio/mp4":   "mp4",
    "audio/ogg":   "ogg",
    "audio/mpeg":  "mp3",
    "audio/m4a":   "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav":   "wav",
    "audio/flac":  "flac",
    "audio/aac":   "aac",
    "video/mp4":   "mp4",
    "video/webm":  "webm",
}

_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client


def transcribe_slack_audio(file_id: str) -> str | None:
    """
    Pobiera plik audio ze Slacka i transkrybuje go przez OpenAI Whisper API.

    Returns:
        Tekst transkrypcji lub None jeśli to nie audio / wystąpił błąd.
    """
    try:
        info = _ctx.app.client.files_info(file=file_id)
        file_obj = info["file"]
        mime = file_obj.get("mimetype", "")

        if mime not in SLACK_AUDIO_MIMES:
            return None

        token = os.environ.get("SLACK_BOT_TOKEN", "")
        url = file_obj.get("url_private_download") or file_obj.get("url_private")
        if not url:
            logger.warning(f"Brak URL do pobrania pliku {file_id}")
            return None

        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()

        ext = _MIME_TO_EXT.get(mime, "mp4")
        logger.info(f"Pobrano audio {file_obj.get('name')} ({len(resp.content) / 1024:.0f} KB), mime={mime}, ext={ext}")

        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            client = _get_openai_client()
            with open(tmp_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="pl",
                )
            transcript = result.text.strip()
            logger.info(f"OpenAI Whisper transkrypcja gotowa ({len(transcript)} znaków): {transcript[:120]!r}")
            return transcript
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Błąd transkrypcji głosówki {file_id}: {type(e).__name__}: {e}", exc_info=True)
        return None
