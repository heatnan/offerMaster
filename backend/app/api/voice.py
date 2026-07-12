"""Voice endpoints: STT + TTS."""
import json
import struct
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import settings
from ..services import voice as voice_service


router = APIRouter(prefix="/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str


@router.post("/stt")
async def stt(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "no file")
    audio = await file.read()
    if not audio:
        raise HTTPException(400, "empty audio")

    ext = ".webm"
    if "." in file.filename:
        ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    out_dir = Path(settings.STORAGE_DIR) / "answers"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = out_dir / fname
    fpath.write_bytes(audio)
    audio_path = str(fpath.relative_to(settings.STORAGE_DIR))

    try:
        text = voice_service.get_stt().transcribe(audio, filename=file.filename)
    except Exception:
        raise

    return {"text": text, "audio_path": audio_path}


@router.websocket("/stt-stream")
async def stt_stream(ws: WebSocket):
    """真流式 STT WebSocket（仅 volcengine_asr）。

    协议（前端 → 后端）:
      - 二进制帧: 音频数据块（webm/opus），边录边发
      - 文本帧 {"done": true}: 表示音频结束

    协议（后端 → 前端）:
      - {"type": "interim", "text": "..."}  中间识别结果
      - {"type": "final",   "text": "...", "audio_path": "..."}  最终结果
      - {"type": "error",   "message": "..."}  错误

    架构：客户端 WS 建连后，后端立即打开与火山 ASR 的 WS，转发前端音频块，
    把火山返回的识别结果推回前端。final 时保存音频文件并返回 audio_path。
    """
    await ws.accept()

    if settings.STT_PROVIDER != "volcengine_asr":
        await ws.send_text(json.dumps({"type": "error", "message": "流式 STT 仅支持 volcengine_asr provider"}))
        await ws.close()
        return

    import asyncio
    import traceback
    import websockets as ext_ws

    api_key = settings.VOLCENGINE_ASR_API_KEY
    app_id = settings.DOUBAO_APP_ID
    access_token = settings.DOUBAO_ACCESS_TOKEN
    if not api_key and not (app_id and access_token):
        await ws.send_text(json.dumps({"type": "error", "message": "未配置火山引擎凭证"}))
        await ws.close()
        return

    RESOURCE_ID = "volc.seedasr.sauc.duration"
    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

    def _pack(header: bytes, payload: bytes) -> bytes:
        return header + struct.pack(">I", len(payload)) + payload

    # 二进制协议 header (4 bytes):
    #   byte[0] = version(4b)=1 | header_size(4b)=1  => 0x11
    #   byte[1] = msg_type(4b)  | flags(4b)
    #   byte[2] = serialization(4b) | compression(4b)  (JSON=0b0001, none=0b0000)
    #   byte[3] = reserved = 0
    # full client request: msg_type=0b0001, flags=0b0000, JSON, no-compress
    FULL_CLIENT_HEADER = bytes([0x11, 0x10, 0x10, 0x00])
    # audio only request: msg_type=0b0010, flags=0b0000, raw, no-compress
    AUDIO_HEADER       = bytes([0x11, 0x20, 0x00, 0x00])
    # audio only request last packet: flags=0b0010
    AUDIO_LAST_HEADER  = bytes([0x11, 0x22, 0x00, 0x00])

    if api_key:
        volcengine_headers = {
            "X-Api-Key":         api_key,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-Request-Id":  uuid.uuid4().hex,
            "X-Api-Sequence":    "-1",
        }
    else:
        volcengine_headers = {
            "X-Api-App-Key":     app_id,
            "X-Api-Access-Key":  access_token,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-Request-Id":  uuid.uuid4().hex,
            "X-Api-Sequence":    "-1",
        }

    req_payload = json.dumps({
        "user": {"uid": "offer-master"},
        # 前端发送裸 PCM (16kHz, 16bit, 单声道, little-endian)
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
        "request": {"model_name": "bigmodel", "enable_punc": True, "enable_itn": True},
    }).encode("utf-8")

    # 边接收边保存所有 PCM 块，用于最终存盘（存为 WAV 便于回放）
    all_audio_parts: list[bytes] = []

    def _parse_server_frame(raw: bytes):
        """解析火山返回的二进制帧，处理可选的 sequence 字段。

        header(4) + [sequence(4) if flags&0b0001] + payload_size(4) + payload
        返回 (resp_dict | None, is_last)
        """
        if not isinstance(raw, bytes) or len(raw) < 8:
            return None, False
        msg_type = (raw[1] >> 4) & 0x0F
        flags = raw[1] & 0x0F
        offset = 4
        seq = None
        if flags & 0b0001:  # 带 sequence number
            seq = struct.unpack(">i", raw[4:8])[0]
            offset = 8
        is_last = bool(flags & 0b0010) or (seq is not None and seq < 0)
        # error 消息 (msg_type=0b1111)
        if msg_type == 0b1111:
            # error: header + [seq] + error_code(4) + payload_size(4) + payload
            err_code = struct.unpack(">I", raw[offset:offset + 4])[0]
            offset += 4
            payload_size = struct.unpack(">I", raw[offset:offset + 4])[0]
            payload = raw[offset + 4:offset + 4 + payload_size]
            print(f"[stt-stream] server error code={err_code} payload={payload[:200]!r}", flush=True)
            return None, True
        payload_size = struct.unpack(">I", raw[offset:offset + 4])[0]
        payload = raw[offset + 4:offset + 4 + payload_size]
        try:
            return json.loads(payload.decode("utf-8")), is_last
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, is_last

    def _save_answer_audio() -> str:
        pcm = b"".join(all_audio_parts)
        if not pcm:
            return ""
        # 把裸 PCM (16kHz/16bit/mono) 封装成 WAV，便于前端回放
        import wave
        out_dir = Path(settings.STORAGE_DIR) / "answers"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.wav"
        fpath = out_dir / fname
        with wave.open(str(fpath), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm)
        return str(fpath.relative_to(settings.STORAGE_DIR))

    try:
        # ping_interval=None: disable websockets library's own keepalive pings.
        # The volcengine server manages its own keepalive; if both sides send pings
        # the ping-pong can race and trigger "keepalive ping timeout" on long answers.
        async with ext_ws.connect(WS_URL, extra_headers=volcengine_headers,
                                  ping_interval=None, ping_timeout=None) as asr_ws:
            # 首先发送 full client request 完成握手
            await asr_ws.send(_pack(FULL_CLIENT_HEADER, req_payload))

            async def forward_client_to_asr():
                """前端 → 后端 → 火山：边收边转发。"""
                nchunks = 0
                try:
                    while True:
                        msg = await ws.receive()
                        if "bytes" in msg and msg["bytes"]:
                            chunk = msg["bytes"]
                            all_audio_parts.append(chunk)
                            await asr_ws.send(_pack(AUDIO_HEADER, chunk))
                            nchunks += 1
                        elif "text" in msg:
                            data = json.loads(msg["text"])
                            if data.get("done"):
                                # 发一个空的 last packet 作为结束标记
                                await asr_ws.send(_pack(AUDIO_LAST_HEADER, b""))
                                return
                        elif msg.get("type") == "websocket.disconnect":
                            return
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    print(f"[stt-stream] forward error: {e}", flush=True)

            async def relay_asr_to_client():
                """火山 → 后端 → 前端：把识别结果推回前端。"""
                final_text = ""
                got_final = False
                nresp = 0
                try:
                    async for raw in asr_ws:
                        nresp += 1
                        resp, is_last = _parse_server_frame(raw)
                        if resp is None:
                            if is_last:
                                break
                            continue

                        result = resp.get("result", {})
                        text = result.get("text", "")
                        is_final = is_last or result.get("is_final", False)

                        if text:
                            final_text = text
                            if is_final:
                                got_final = True
                                audio_path = _save_answer_audio()
                                await ws.send_text(json.dumps({
                                    "type": "final", "text": text, "audio_path": audio_path,
                                }, ensure_ascii=False))
                            else:
                                await ws.send_text(json.dumps({
                                    "type": "interim", "text": text,
                                }, ensure_ascii=False))

                        if is_final:
                            break
                except Exception as e:
                    print(f"[stt-stream] relay error: {e}", flush=True)

                # 兜底：如果没收到明确的 final 但连接结束了，发一次 final
                if final_text and not got_final:
                    audio_path = _save_answer_audio()
                    try:
                        await ws.send_text(json.dumps({
                            "type": "final", "text": final_text, "audio_path": audio_path,
                        }, ensure_ascii=False))
                    except Exception:
                        pass

            await asyncio.gather(forward_client_to_asr(), relay_asr_to_client())

    except Exception as e:
        print(f"[stt-stream] fatal: {e}\n{traceback.format_exc()}", flush=True)
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass

    try:
        await ws.close()
    except Exception:
        pass


@router.post("/tts")
def tts(req: TTSRequest):
    audio = voice_service.get_tts().synthesize(req.text)
    return Response(content=audio, media_type="audio/mpeg")
