"""STT / TTS provider abstraction.

STT: local_whisper (faster-whisper) | volcengine_asr | openai_compat
TTS: edge (edge-tts, free)          | doubao          | openai_compat
"""
from __future__ import annotations
import asyncio
import io
import os
import time
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
    if settings.STT_PROVIDER == "volcengine_asr":
        return VolcengineASR()
    return OpenAICompatSTT()


class VolcengineASR:
    """火山引擎豆包流式语音识别 2.0 — WebSocket 接入。

    凭证与 TTS 共用同一个应用（相同 APP ID + Access Token）。
    资源 ID 对应"小时版"：volc.seedasr.sauc.duration
    接口：wss://openspeech.bytedance.com/api/v3/sauc/bigmodel

    协议：4 字节二进制 Header + 4 字节 Payload Size (big-endian) + JSON/Audio Payload
    - Full client request (header 0x11 0x10 0x11 0x00): JSON 配置
    - Audio only request  (header 0x12 0x02 0x00 0x00): 音频数据，最后一包 flags=0b0010
    - 返回 Full server response: JSON，result.text 为识别文本
    """

    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    RESOURCE_ID = "volc.seedasr.sauc.duration"

    # 二进制协议 header (4 bytes)
    _FULL_CLIENT_HEADER = bytes([0x11, 0x10, 0x10, 0x00])
    _AUDIO_HEADER       = bytes([0x11, 0x20, 0x00, 0x00])
    _AUDIO_LAST_HEADER  = bytes([0x11, 0x22, 0x00, 0x00])

    def __init__(self):
        if not (settings.DOUBAO_APP_ID and settings.DOUBAO_ACCESS_TOKEN) and not settings.VOLCENGINE_ASR_API_KEY:
            raise RuntimeError(
                "STT_PROVIDER=volcengine_asr 但未配置凭证。"
                "请在 .env 里填入 VOLCENGINE_ASR_API_KEY（新版控制台）"
                "或 DOUBAO_APP_ID + DOUBAO_ACCESS_TOKEN（旧版控制台）。"
            )

    @staticmethod
    def _pack(header: bytes, payload: bytes) -> bytes:
        import struct
        size = struct.pack(">I", len(payload))
        return header + size + payload

    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        import json
        import struct
        import concurrent.futures

        async def _run() -> str:
            import websockets

            # 新版控制台用 X-Api-Key；旧版用 X-Api-App-Key + X-Api-Access-Key
            if settings.VOLCENGINE_ASR_API_KEY:
                headers = {
                    "X-Api-Key": settings.VOLCENGINE_ASR_API_KEY,
                    "X-Api-Resource-Id": self.RESOURCE_ID,
                    "X-Api-Request-Id": uuid.uuid4().hex,
                    "X-Api-Sequence": "-1",
                }
            else:
                headers = {
                    "X-Api-App-Key": settings.DOUBAO_APP_ID,
                    "X-Api-Access-Key": settings.DOUBAO_ACCESS_TOKEN,
                    "X-Api-Resource-Id": self.RESOURCE_ID,
                    "X-Api-Request-Id": uuid.uuid4().hex,
                    "X-Api-Sequence": "-1",
                }

            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
            if ext in ("webm", "ogg"):
                fmt, codec = "ogg", "opus"
            elif ext == "wav":
                fmt, codec = "wav", "raw"
            elif ext == "mp3":
                fmt, codec = "mp3", "raw"
            else:
                fmt, codec = "ogg", "opus"

            req_payload = json.dumps({
                "user": {"uid": "offer-master"},
                "audio": {
                    "format": fmt,
                    "codec": codec,
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_punc": True,
                    "enable_itn": True,
                },
            }).encode("utf-8")

            full_client_req = self._pack(self._FULL_CLIENT_HEADER, req_payload)

            chunk_size = 32 * 1024
            chunks = [audio_bytes[i:i + chunk_size] for i in range(0, len(audio_bytes), chunk_size)]

            async with websockets.connect(self.WS_URL, extra_headers=headers) as ws:
                await ws.send(full_client_req)

                for i, chunk in enumerate(chunks):
                    is_last = (i == len(chunks) - 1)
                    hdr = self._AUDIO_LAST_HEADER if is_last else self._AUDIO_HEADER
                    await ws.send(self._pack(hdr, chunk))

                # 火山流式返回的 text 是累积全量文本，取最后一个即可
                final_text = ""
                async for raw in ws:
                    if not isinstance(raw, bytes) or len(raw) < 8:
                        continue
                    # header(4) + [sequence(4) if flags&0b0001] + payload_size(4) + payload
                    msg_type = (raw[1] >> 4) & 0x0F
                    flags = raw[1] & 0x0F
                    offset = 4
                    seq = None
                    if flags & 0b0001:
                        seq = struct.unpack(">i", raw[4:8])[0]
                        offset = 8
                    is_last = bool(flags & 0b0010) or (seq is not None and seq < 0)
                    if msg_type == 0b1111:  # error
                        break
                    payload_size = struct.unpack(">I", raw[offset:offset + 4])[0]
                    payload_bytes = raw[offset + 4:offset + 4 + payload_size]
                    try:
                        resp = json.loads(payload_bytes.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if is_last:
                            break
                        continue
                    result = resp.get("result", {})
                    text = result.get("text", "")
                    if text:
                        final_text = text
                    if is_last or result.get("is_final"):
                        break

            return final_text.strip()

        # FastAPI runs inside an event loop; asyncio.run() would conflict.
        # Run the coroutine in a fresh thread with its own event loop instead.
        def _run_in_thread() -> str:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_run())
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=60)


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


class DoubaoTTS:
    """火山引擎（豆包）语音合成大模型 TTS provider.

    Doc: https://www.volcengine.com/docs/6561/1257544

    Optional upgrade for users who want a much more natural, emotionally-
    expressive Chinese voice than free Edge TTS.  Requires opening a
    Volcengine account and filling DOUBAO_* in .env.

    Falls back to raising an informative error if credentials are missing,
    so users don't get a cryptic 401 from the API.
    """

    API_URL = "https://openspeech.bytedance.com/api/v1/tts"

    def __init__(self):
        if not (settings.DOUBAO_APP_ID and settings.DOUBAO_ACCESS_TOKEN):
            raise RuntimeError(
                "TTS_PROVIDER=doubao 但未配置 DOUBAO_APP_ID / DOUBAO_ACCESS_TOKEN。"
                "请在 .env 里填入火山引擎语音合成服务的凭证，或改用 TTS_PROVIDER=edge。"
            )

    def synthesize(self, text: str) -> bytes:
        import json
        import base64
        import urllib.request
        import urllib.error

        payload = {
            "app": {
                "appid": settings.DOUBAO_APP_ID,
                "token": settings.DOUBAO_ACCESS_TOKEN,
                "cluster": settings.DOUBAO_CLUSTER,
            },
            "user": {"uid": "offer-master"},
            "audio": {
                "voice_type": settings.DOUBAO_VOICE_TYPE,
                "encoding": "mp3",
                "speed_ratio": 1.0,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": uuid.uuid4().hex,
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }
        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # 豆包用 Bearer;{token} 这种非标准格式
                "Authorization": f"Bearer;{settings.DOUBAO_ACCESS_TOKEN}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Doubao TTS HTTP {e.code}: {body}") from e

        if data.get("code") != 3000:
            raise RuntimeError(f"Doubao TTS error: {data.get('code')} {data.get('message')}")
        audio_b64 = data.get("data")
        if not audio_b64:
            raise RuntimeError(f"Doubao TTS empty audio: {data}")
        return base64.b64decode(audio_b64)


def get_tts() -> TTSProvider:
    if settings.TTS_PROVIDER == "edge":
        return EdgeTTS()
    if settings.TTS_PROVIDER == "doubao":
        return DoubaoTTS()
    return OpenAICompatTTS()


def save_tts(text: str, subdir: str = "tts") -> str:
    """Synthesize and persist to storage; return relative path.

    Retries a couple of times because the network path to cloud TTS providers
    (e.g. Docker Desktop's DNS resolver) can blip transiently — a short retry
    self-heals those without failing the whole question.
    """
    out_dir = Path(settings.STORAGE_DIR) / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.mp3"
    fpath = out_dir / fname
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            fpath.write_bytes(get_tts().synthesize(text))
            return str(fpath.relative_to(settings.STORAGE_DIR))
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    raise last_err  # type: ignore[misc]

