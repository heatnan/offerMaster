from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_title: Mapped[str] = mapped_column(String(255))
    jd_text: Mapped[str] = mapped_column(Text)
    resume_text: Mapped[str] = mapped_column(Text)
    rounds_planned: Mapped[int] = mapped_column(Integer, default=1)  # 1~3
    status: Mapped[str] = mapped_column(String(32), default="created")
    # created | round_running | round_finished | passed | failed | completed
    current_round_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    rounds: Mapped[list["Round"]] = relationship(back_populates="interview", cascade="all, delete-orphan")
    report: Mapped["Report"] = relationship(back_populates="interview", uselist=False, cascade="all, delete-orphan")


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"))
    round_no: Mapped[int] = mapped_column(Integer)  # 1/2/3
    role: Mapped[str] = mapped_column(String(32))   # peer | high_peer | manager
    status: Mapped[str] = mapped_column(String(32), default="planned")
    # planned | running | passed | failed
    score: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[str] = mapped_column(Text, default="")

    interview: Mapped[Interview] = relationship(back_populates="rounds")
    questions: Mapped[list["Question"]] = relationship(back_populates="round_", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"))
    seq: Mapped[int] = mapped_column(Integer)
    topic: Mapped[str] = mapped_column(String(255), default="")
    question_text: Mapped[str] = mapped_column(Text)
    is_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tts_path: Mapped[str] = mapped_column(String(512), default="")

    round_: Mapped[Round] = relationship(back_populates="questions")
    answer: Mapped["Answer"] = relationship(back_populates="question", uselist=False, cascade="all, delete-orphan")
    score_: Mapped["Score"] = relationship(back_populates="question", uselist=False, cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    transcript: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str] = mapped_column(String(512), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    question: Mapped[Question] = relationship(back_populates="answer")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    dimensions: Mapped[dict] = mapped_column(JSON)  # {"tech":8,"expression":7,"depth":6}
    total: Mapped[float] = mapped_column(Float)
    comment: Mapped[str] = mapped_column(Text, default="")

    question: Mapped[Question] = relationship(back_populates="score_")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"), unique=True)
    overall_comment_md: Mapped[str] = mapped_column(Text)
    pdf_path: Mapped[str] = mapped_column(String(512), default="")

    interview: Mapped[Interview] = relationship(back_populates="report")
