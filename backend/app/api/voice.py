"""Voice endpoints: STT + TTS."""
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
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

    # Persist the candidate's answer audio so it can be replayed / audited later.
    # Path is returned to the frontend so it can be sent along with submit_answer
    # and stored on the Answer row (Answer.audio_path).
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
        # Even on transcription failure we keep the audio file so the user
        # can still recover their answer by listening to it.
        raise

    return {"text": text, "audio_path": audio_path}


@router.post("/tts")
def tts(req: TTSRequest):
    audio = voice_service.get_tts().synthesize(req.text)
    return Response(content=audio, media_type="audio/mpeg")
