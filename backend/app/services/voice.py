"""STT / TTS provider abstraction.

STT: local_whisper (faster-whisper) | openai_compat
TTS: edge (edge-tts, free)          | openai_compat
"""
from __future__ import annotations
import asyncio
import io
import os
import uuid
from pathlib import Path
from typing import Protocol

from ..config import settings


# ------------- STT -------------

class STTProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str: ...


class LocalWhisperSTT:
    _model = None
    _t2s = None  # OpenCC 繁体->简体 converter

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            from faster_whisper import WhisperModel
            cls._model = WhisperModel(
                settings.WHISPER_MODEL,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
        return cls._model

    @classmethod
    def _get_t2s(cls):
        if cls._t2s is None:
            from opencc import OpenCC
            cls._t2s = OpenCC("t2s")  # traditional -> simplified
        return cls._t2s

    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        # faster-whisper accepts a file path or file-like; use temp file for compatibility with webm/opus
        tmp_dir = Path(settings.STORAGE_DIR) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{filename}"
        tmp_path.write_bytes(audio_bytes)
        try:
            segments, _ = self._get_model().transcribe(
                str(tmp_path),
                language="zh",
                vad_filter=True,
                # Whisper 默认对中文不产生标点。用 initial_prompt 引导它输出带标点的普通话简体文本。
                initial_prompt="以下是普通话的对话，请使用简体中文和中文标点符号，包括逗号、句号、问号等。",
                condition_on_previous_text=False,
            )
            text = "".join(seg.text for seg in segments).strip()
            # Whisper 中文模型有时会输出繁体，用 OpenCC 强制转换成简体
            return self._get_t2s().convert(text)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass


class OpenAICompatSTT:
    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        from .llm import get_client
        resp = get_client().audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, "audio/webm"),
            language="zh",
        )
        return resp.text


def get_stt() -> STTProvider:
    if settings.STT_PROVIDER == "local_whisper":
        return LocalWhisperSTT()
    return OpenAICompatSTT()


# ------------- TTS -------------

class TTSProvider(Protocol):
    def synthesize(self, text: str) -> bytes: ...


class EdgeTTS:
    def synthesize(self, text: str) -> bytes:
        import edge_tts

        async def _run() -> bytes:
            comm = edge_tts.Communicate(text, settings.TTS_VOICE)
            buf = io.BytesIO()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        return asyncio.run(_run())


class OpenAICompatTTS:
    def synthesize(self, text: str) -> bytes:
        from .llm import get_client
        resp = get_client().audio.speech.create(
            model="tts-1",
            voice="onyx",
            input=text,
            response_format="mp3",
        )
        return resp.read()


def get_tts() -> TTSProvider:
    if settings.TTS_PROVIDER == "edge":
        return EdgeTTS()
    return OpenAICompatTTS()


def save_tts(text: str, subdir: str = "tts") -> str:
    """Synthesize and persist to storage; return relative path."""
    out_dir = Path(settings.STORAGE_DIR) / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.mp3"
    fpath = out_dir / fname
    fpath.write_bytes(get_tts().synthesize(text))
    return str(fpath.relative_to(settings.STORAGE_DIR))
