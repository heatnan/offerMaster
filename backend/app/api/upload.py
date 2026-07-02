"""Resume / JD upload -> text extraction."""
from fastapi import APIRouter, UploadFile, File, HTTPException

from ..services import resume as resume_service


router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/resume")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    try:
        text = resume_service.extract(file.filename or "resume", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"text": text, "chars": len(text)}
