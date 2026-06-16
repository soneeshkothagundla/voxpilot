"""Speech-to-text using the OpenAI audio transcription HTTP API.

This backend talks to ``https://api.openai.com/v1/audio/transcriptions``
directly via ``httpx`` multipart upload (no ``openai`` dependency). Float32
audio arrays are written to a temporary 16 kHz mono 16-bit WAV using the
standard library ``wave`` module before upload.
"""

from __future__ import annotations

import os
import tempfile
import wave

import numpy as np

from .base import STTBackend

__all__ = ["OpenAIWhisperSTT"]

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
_SAMPLE_RATE = 16000


class OpenAIWhisperSTT(STTBackend):
    """Speech-to-text backend using the OpenAI transcription HTTP API."""

    def __init__(self, api_key: str | None, model: str = "whisper-1") -> None:
        """Initialize the backend.

        Args:
            api_key: OpenAI API key (required at transcription time).
            model: Transcription model name (default ``"whisper-1"``).
        """
        self.api_key = api_key
        self.model = model

    def _write_wav(self, audio: np.ndarray) -> str:
        """Write a float32 mono array to a temp 16 kHz 16-bit WAV file.

        Args:
            audio: Float32 mono audio in [-1, 1].

        Returns:
            The path to the temporary WAV file (caller must delete it).
        """
        clipped = np.clip(audio.reshape(-1).astype(np.float32), -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="voxpilot_")
            os.close(fd)
            with wave.open(path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(_SAMPLE_RATE)
                wav.writeframes(pcm.tobytes())
        except OSError as exc:
            raise RuntimeError(
                f"Failed to write temp audio file ({exc}); check disk space/permissions."
            ) from exc
        return path

    def transcribe(self, audio: np.ndarray | str) -> str:
        """Transcribe audio via the OpenAI transcription endpoint.

        Args:
            audio: A float32 mono 16 kHz ``np.ndarray`` or a path to an audio
                file.

        Returns:
            The transcribed text, stripped.

        Raises:
            RuntimeError: If no API key is configured.
        """
        if not self.api_key:
            raise RuntimeError(
                "OpenAI API key is required for the OpenAI STT backend (set OPENAI_API_KEY)."
            )

        import httpx

        temp_path: str | None = None
        if isinstance(audio, np.ndarray):
            if audio.size == 0:
                return ""
            temp_path = self._write_wav(audio)
            file_path = temp_path
        else:
            file_path = audio

        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data = {"model": self.model}
            with open(file_path, "rb") as handle:
                files = {"file": (os.path.basename(file_path), handle, "audio/wav")}
                response = httpx.post(
                    _API_URL,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=120.0,
                )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("text", "")).strip()
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
