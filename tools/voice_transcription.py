"""Transkrypcja wiadomości głosowych ze Slacka przez OpenAI Whisper API."""
import logging
import os
import tempfile
import time

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


def _is_audio_mime(mime: str) -> bool:
    """Accept audio/* and video/* mimes, including those with codec suffixes like audio/webm;codecs=opus."""
    base = mime.split(";")[0].strip().lower()
    if base in SLACK_AUDIO_MIMES:
        return True
    # Fallback: any audio/* or video/* counts as audio
    return base.startswith("audio/") or base.startswith("video/")


def _get_ext(mime: str) -> str:
    base = mime.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(base, "mp4")


def transcribe_slack_audio(file_id: str) -> str | None:
    """
    Pobiera plik audio ze Slacka i transkrybuje go przez OpenAI Whisper API.
    Retries up to 3 times to handle files still being processed by Slack.

    Returns:
        Tekst transkrypcji lub None jeśli to nie audio / wystąpił błąd.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")

    for attempt in range(1, 4):
        try:
            info = _ctx.app.client.files_info(file=file_id)
            file_obj = info["file"]
            mime = file_obj.get("mimetype", "")

            logger.info(
                f"[attempt {attempt}] files_info {file_id}: mime={mime!r}"
                f" subtype={file_obj.get('subtype')!r}"
                f" mode={file_obj.get('mode')!r}"
                f" size={file_obj.get('size')}"
            )

            if not _is_audio_mime(mime) and file_obj.get("subtype") != "slack_audio":
                logger.info(f"Pomijam plik {file_id}: mime={mime!r} nie jest audio")
                return None

            # Try various URL fields — Slack uses different ones depending on file type/age
            url = (
                file_obj.get("url_private_download")
                or file_obj.get("url_private")
                or file_obj.get("mp4_64")
                or file_obj.get("permalink")
            )
            if not url:
                logger.warning(f"[attempt {attempt}] Brak URL do pobrania pliku {file_id}, file_obj keys: {list(file_obj.keys())}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                return None

            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
            if resp.status_code in (404, 403) and attempt < 3:
                logger.warning(f"[attempt {attempt}] HTTP {resp.status_code} dla {url} — retry za {2*attempt}s")
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()

            # Prefer Slack's filetype field (e.g. 'm4a') over mime-derived ext —
            # audio/mp4 maps to 'mp4' which OpenAI rejects, but 'm4a' is accepted.
            ext = file_obj.get("filetype") or _get_ext(mime)
            size_kb = len(resp.content) / 1024
            logger.info(f"Pobrano audio {file_obj.get('name')!r} ({size_kb:.0f} KB), mime={mime}, ext={ext}")

            if size_kb < 0.5:
                logger.warning(f"Plik {file_id} za mały ({size_kb:.1f} KB) — prawdopodobnie jeszcze nie gotowy")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                return None

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
                logger.info(f"Whisper transkrypcja gotowa ({len(transcript)} znaków): {transcript[:120]!r}")
                return transcript
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.error(
                f"[attempt {attempt}] Błąd transkrypcji głosówki {file_id}: {type(e).__name__}: {e}",
                exc_info=True,
            )
            if attempt < 3:
                time.sleep(2 * attempt)
            else:
                return None

    return None
