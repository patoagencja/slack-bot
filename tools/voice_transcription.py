"""Transkrypcja wiadomości głosowych ze Slacka przez Claude."""
import base64
import logging
import os

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
}


def transcribe_slack_audio(file_id: str) -> str | None:
    """
    Pobiera plik audio ze Slacka i transkrybuje go przez Claude.

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

        audio_b64 = base64.standard_b64encode(resp.content).decode("utf-8")
        logger.info(f"Pobrano audio {file_obj.get('name')} ({len(resp.content) / 1024:.0f} KB), mime={mime}")

        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "audio",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": audio_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Przetranskrybuj dokładnie tę wiadomość głosową. "
                            "Zwróć tylko sam tekst transkrypcji, bez żadnych komentarzy ani wyjaśnień."
                        ),
                    },
                ],
            }],
        )

        transcript = response.content[0].text.strip()
        logger.info(f"Transkrypcja gotowa ({len(transcript)} znaków): {transcript[:120]!r}")
        return transcript

    except Exception as e:
        logger.error(f"Błąd transkrypcji głosówki {file_id}: {e}")
        return None
