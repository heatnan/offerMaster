"""Interview agent — orchestrates question generation, follow-up decisions, scoring, and reports.

Implemented as functional nodes (rather than a fully autonomous graph) because the interview
is turn-based and gated on human voice input. The FastAPI layer drives the state machine.
LangGraph is still used to compose the reusable per-turn subgraph (answer -> decide -> score).
"""
from __future__ import annotations
from typing import TypedDict

from ..config import settings
from ..services import llm
from . import prompts
from .rules import INTRO_QUESTION


# ---------- Node: plan questions for a round ----------

def plan_questions(resume: str, jd: str, role: str, round_no: int = 1) -> list[dict]:
    n_min = settings.QUESTIONS_PER_ROUND_MIN
    n_max = settings.QUESTIONS_PER_ROUND_MAX
    n = (n_min + n_max) // 2
    role_desc = prompts.ROLE_DESC.get(role, prompts.ROLE_DESC["peer"])
    data = llm.chat_json(
        messages=[
            {"role": "system", "content": prompts.PLAN_QUESTIONS_SYSTEM},
            {
                "role": "user",
                "content": prompts.PLAN_QUESTIONS_USER.format(
                    role_desc=role_desc,
                    n=n, n_min=n_min, n_max=n_max,
                    resume=resume[:6000],
                    jd=jd[:3000],
                ),
            },
        ],
        temperature=0.5,
    )
    questions = data.get("questions", [])
    # normalize + dedupe (LLMs sometimes emit near-identical items for the
    # same topic — we drop exact-text duplicates and also collapse duplicates
    # by topic to avoid asking two questions on the same subject).
    seen_q: set[str] = set()
    seen_topic: set[str] = set()
    result = []
    for q in questions[:n_max]:
        topic = (q.get("topic") or "").strip()[:200]
        question = (q.get("question") or "").strip()
        if not question:
            continue
        key = question
        if key in seen_q:
            continue
        topic_key = topic.lower()
        if topic_key and topic_key in seen_topic:
            continue
        seen_q.add(key)
        if topic_key:
            seen_topic.add(topic_key)
        result.append({"topic": topic, "question": question})
    # Rule: the very first round always opens with a self-introduction.
    if round_no == 1:
        result.insert(0, dict(INTRO_QUESTION))
    return result


# ---------- Node: decide follow-up ----------

class FollowupDecision(TypedDict):
    action: str  # "followup" | "next"
    followup_question: str
    reason: str


def decide_followup(topic: str, question: str, answer: str, followups_so_far: int) -> FollowupDecision:
    if followups_so_far >= settings.MAX_FOLLOWUPS:
        return {"action": "next", "followup_question": "", "reason": "达到最大追问次数"}
    data = llm.chat_json(
        messages=[
            {
                "role": "system",
                "content": prompts.FOLLOWUP_DECIDE_SYSTEM.format(
                    followups_so_far=followups_so_far,
                    max_followups=settings.MAX_FOLLOWUPS,
                    strategy=settings.FOLLOWUP_STRATEGY,
                ),
            },
            {
                "role": "user",
                "content": prompts.FOLLOWUP_DECIDE_USER.format(
                    topic=topic, question=question, answer=answer
                ),
            },
        ],
        temperature=0.4,
    )
    action = data.get("action", "next")
    if action not in ("followup", "next"):
        action = "next"
    return {
        "action": action,
        "followup_question": (data.get("followup_question") or "").strip(),
        "reason": (data.get("reason") or "").strip(),
    }


# ---------- Node: score a question ----------

def score_answer(question: str, answer: str) -> dict:
    data = llm.chat_json(
        messages=[
            {"role": "system", "content": prompts.SCORE_SYSTEM},
            {"role": "user", "content": prompts.SCORE_USER.format(question=question, answer=answer)},
        ],
        temperature=0.2,
    )
    dims = data.get("dimensions") or {}
    dims = {
        "technical": _clip(dims.get("technical", 0)),
        "expression": _clip(dims.get("expression", 0)),
        "depth": _clip(dims.get("depth", 0)),
    }
    total = data.get("total")
    if not isinstance(total, (int, float)):
        total = round(sum(dims.values()) / 3 * 10, 1)
    return {
        "dimensions": dims,
        "total": float(total),
        "comment": (data.get("comment") or "").strip(),
    }


def _clip(v, lo=0, hi=10):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0
    return max(lo, min(hi, v))


# ---------- Node: round summary ----------

def summarize_round(role: str, qa_log: str, score: float) -> str:
    return llm.chat(
        messages=[
            {"role": "system", "content": prompts.ROUND_SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": prompts.ROUND_SUMMARY_USER.format(
                    role_desc=prompts.ROLE_DESC.get(role, ""),
                    qa_log=qa_log,
                    score=score,
                ),
            },
        ],
        temperature=0.5,
    )


# ---------- Node: final report ----------

def final_report(position: str, rounds_log: str, qa_log: str) -> str:
    return llm.chat(
        messages=[
            {"role": "system", "content": prompts.FINAL_REPORT_SYSTEM},
            {
                "role": "user",
                "content": prompts.FINAL_REPORT_USER.format(
                    position=position, rounds_log=rounds_log, qa_log=qa_log
                ),
            },
        ],
        temperature=0.4,
    )
