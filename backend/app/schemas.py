from typing import Optional
from pydantic import BaseModel, field_validator


class InterviewCreate(BaseModel):
    position_title: str

    @field_validator("position_title")
    @classmethod
    def position_title_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 255:
            raise ValueError("岗位名称不能超过 255 字符，请只填写职位名称（如：后端工程师）")
        return v
    jd_text: str
    resume_text: str
    rounds_planned: int = 1
    # Which round to begin at (1=peer, 2=high_peer, 3=manager). Lets the user
    # jump straight into e.g. the manager round to practice it in isolation.
    start_round: int = 1


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
