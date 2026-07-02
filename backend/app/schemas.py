from typing import Optional
from pydantic import BaseModel


class InterviewCreate(BaseModel):
    position_title: str
    jd_text: str
    resume_text: str
    rounds_planned: int = 1


class InterviewOut(BaseModel):
    id: int
    position_title: str
    status: str
    rounds_planned: int
    current_round_no: int

    class Config:
        from_attributes = True


class QuestionOut(BaseModel):
    id: int
    seq: int
    topic: str
    question_text: str
    is_followup: bool
    tts_url: Optional[str] = None

    class Config:
        from_attributes = True


class AnswerIn(BaseModel):
    question_id: int
    transcript: str
    audio_path: str = ""
    duration_ms: int = 0


class STTResult(BaseModel):
    text: str


class ScoreOut(BaseModel):
    dimensions: dict
    total: float
    comment: str


class RoundOut(BaseModel):
    id: int
    round_no: int
    role: str
    status: str
    score: float
    passed: bool
    feedback: str

    class Config:
        from_attributes = True
