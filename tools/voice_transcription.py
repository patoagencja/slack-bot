"""Transkrypcja wiadomości głosowych ze Slacka przez lokalny model Whisper (bez API)."""
import io
import logging
import os
import tempfile

import requests

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

_whisper_model = None
_WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "tiny")


def _setup_ffmpeg():
    """Ustaw ścieżkę do ffmpeg z imageio-ffmpeg (działa bez apt-get)."""
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_dir = os.path.dirname(ffmpeg_exe)
        os.environ["PATH"] = ffmpeg_dir + ":" + os.environ.get("PATH", "")
        logger.info(f"ffmpeg z imageio-ffmpeg: {ffmpeg_exe}")
    except Exception as e:
        logger.warning(f"imageio-ffmpeg niedostępny, używam systemowego ffmpeg: {e}")


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _setup_ffmpeg()
        import whisper
        logger.info(f"Ładowanie lokalnego modelu Whisper '{_WHISPER_MODEL_SIZE}'...")
        _whisper_model = whisper.load_model(_WHISPER_MODEL_SIZE)
        logger.info("Model Whisper załadowany.")
    return _whisper_model


def transcribe_slack_audio(file_id: str) -> str | None:
    """
    Pobiera plik audio ze Slacka i transkrybuje go lokalnym modelem Whisper.

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
            model = _get_whisper_model()
            result = model.transcribe(tmp_path, language="pl")
            transcript = result["text"].strip()
            logger.info(f"Whisper transkrypcja gotowa ({len(transcript)} znaków): {transcript[:120]!r}")
            return transcript
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Błąd transkrypcji głosówki {file_id}: {type(e).__name__}: {e}", exc_info=True)
        return None
