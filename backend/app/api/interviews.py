"""Interview REST API — turn-based, driven by frontend."""
from __future__ import annotations
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, SessionLocal
from .. import models, schemas
from ..agent import nodes
from ..agent.rules import is_end_signal, INTRO_QUESTION
from ..services import voice as voice_service, report_pdf


router = APIRouter(prefix="/interviews", tags=["interview"])


ROLE_BY_ROUND = {1: "peer", 2: "high_peer", 3: "manager"}


# Tracks in-flight background score threads per round_id so we can wait for
# them before finalizing a round (otherwise the last answer's score may be
# missing from the aggregate + final report).
_pending_scores: dict[int, list[threading.Thread]] = {}
_pending_scores_lock = threading.Lock()


def _score_and_save_async(question_id: int, question_text: str, answer_text: str):
    """Run score_answer in the background and persist the Score row.

    Uses UPSERT semantics: if a Score row already exists for this question
    (e.g. duplicate submit, page refresh, retry) we UPDATE it instead of
    inserting another row. Score↔Question is logically 1:1, so multiple
    Score rows would poison Question.score_ (uselist=False relationship).
    """
    db = SessionLocal()
    try:
        s = nodes.score_answer(question_text, answer_text)
        existing = db.query(models.Score).filter_by(question_id=question_id).first()
        if existing:
            existing.dimensions = s["dimensions"]
            existing.total = s["total"]
            existing.comment = s["comment"]
        else:
            db.add(models.Score(
                question_id=question_id,
                dimensions=s["dimensions"],
                total=s["total"],
                comment=s["comment"],
            ))
        db.commit()
    except Exception as e:
        print(f"[async score] error on q{question_id}: {e}")
    finally:
        db.close()


def _register_pending_score(round_id: int, thread: threading.Thread):
    with _pending_scores_lock:
        _pending_scores.setdefault(round_id, []).append(thread)


def _wait_pending_scores(round_id: int, db: Session, timeout: float = 60.0):
    """Wait for all background score threads for this round to finish.

    Belt-and-suspenders:
    1. Join tracked threads (in-process tracking).
    2. Poll the DB: keep waiting until every answered Question has a Score
       row, up to `timeout` seconds total. Covers the case where the process
       restarted between submit_answer and _finalize_round and lost the
       thread registry.
    """
    import time
    with _pending_scores_lock:
        threads = _pending_scores.pop(round_id, [])

    deadline = time.time() + timeout
    for t in threads:
        remaining = max(0.0, deadline - time.time())
        if t.is_alive() and remaining > 0:
            t.join(timeout=remaining)
        if t.is_alive():
            print(f"[warn] score thread still alive after timeout for round {round_id}")

    # DB-side check: wait until every answered question has a Score row.
    while time.time() < deadline:
        # count answered questions in this round with NO score row
        missing = (
            db.query(models.Question.id)
            .join(models.Answer, models.Answer.question_id == models.Question.id)
            .outerjoin(models.Score, models.Score.question_id == models.Question.id)
            .filter(models.Question.round_id == round_id)
            .filter(models.Score.id.is_(None))
            .count()
        )
        if missing == 0:
            return
        time.sleep(0.5)
    print(f"[warn] round {round_id} finalizing with {missing} unscored answers")


def _tts_url(rel_path: str) -> str:
    return f"/files/{rel_path}"


# ---------- Create ----------

@router.post("", response_model=schemas.InterviewOut)
def create_interview(payload: schemas.InterviewCreate, db: Session = Depends(get_db)):
    if not 1 <= payload.rounds_planned <= 3:
        raise HTTPException(400, "rounds_planned must be 1..3")
    interview = models.Interview(
        position_title=payload.position_title,
        jd_text=payload.jd_text,
        resume_text=payload.resume_text,
        rounds_planned=payload.rounds_planned,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return interview


@router.get("/{interview_id}", response_model=schemas.InterviewOut)
def get_interview(interview_id: int, db: Session = Depends(get_db)):
    obj = db.get(models.Interview, interview_id)
    if not obj:
        raise HTTPException(404)
    return obj


# ---------- Rounds ----------

@router.post("/{interview_id}/rounds/start")
def start_next_round(interview_id: int, db: Session = Depends(get_db)):
    """Fast path: return immediately with the fixed self-introduction as Q1 (round 1
    only). Actual per-role questions are generated & TTS-cached asynchronously in the
    background so the candidate can start answering right away."""
    interview = db.get(models.Interview, interview_id)
    if not interview:
        raise HTTPException(404)

    next_no = interview.current_round_no + 1
    if next_no > interview.rounds_planned:
        raise HTTPException(400, "no more rounds planned")

    role = ROLE_BY_ROUND.get(next_no, "peer")
    round_ = models.Round(
        interview_id=interview.id, round_no=next_no, role=role, status="running"
    )
    db.add(round_)
    db.flush()

    interview.current_round_no = next_no
    interview.status = "round_running"

    if next_no == 1:
        # Round 1: seed the self-intro question synchronously so UI has something instantly.
        intro = models.Question(
            round_id=round_.id, seq=1,
            topic=INTRO_QUESTION["topic"],
            question_text=INTRO_QUESTION["question"],
        )
        db.add(intro)
        db.commit()
        db.refresh(intro)
        # Synthesize the intro TTS synchronously — it's a short fixed sentence (~2s)
        # so users hear audio the moment the page loads.
        _ensure_tts(intro, db)
        # Meanwhile kick off LLM planning + remaining-question TTS in background
        threading.Thread(
            target=_plan_and_tts_async,
            args=(interview.id, round_.id, role, next_no, True),
            daemon=True,
        ).start()
        first_q = intro
    else:
        # Subsequent rounds: generate questions synchronously (needed for next-question flow)
        # and TTS only the FIRST question synchronously so audio plays instantly.
        # Rest of the questions get TTS'd in background.
        questions = nodes.plan_questions(
            interview.resume_text, interview.jd_text, role, round_no=next_no
        )
        if not questions:
            raise HTTPException(500, "failed to plan questions")
        for i, q in enumerate(questions, start=1):
            db.add(models.Question(
                round_id=round_.id, seq=i, topic=q["topic"], question_text=q["question"],
            ))
        db.commit()
        first_q = db.query(models.Question).filter_by(round_id=round_.id).order_by(models.Question.seq).first()
        _ensure_tts(first_q, db)
        threading.Thread(
            target=_tts_all_async, args=(round_.id,), daemon=True
        ).start()

    db.refresh(round_)
    # For UI: give a placeholder tts_url; frontend will poll or re-fetch when needed
    return {
        "round": schemas.RoundOut.model_validate(round_).model_dump(),
        "question": _question_to_out(first_q).model_dump(),
    }


# Guards to make sure _plan_and_tts_async only ever runs once per round even
# if `/rounds/start` is called twice (double-click, retry, page refresh).
_planning_rounds: set[int] = set()
_planning_lock = threading.Lock()


def _plan_and_tts_async(interview_id: int, round_id: int, role: str, round_no: int, has_intro: bool):
    """Background: generate remaining questions for the round and pre-render TTS."""
    # Claim the round; if someone already claimed it, bail.
    with _planning_lock:
        if round_id in _planning_rounds:
            print(f"[async plan] round {round_id} already being planned — skipping")
            return
        _planning_rounds.add(round_id)
    db = SessionLocal()
    try:
        # DB-side guard: if the round already contains non-intro (or non-followup)
        # questions beyond the intro, another planner has already filled it.
        # This survives process restarts (where _planning_rounds gets reset).
        existing_non_intro = (
            db.query(models.Question)
            .filter(models.Question.round_id == round_id)
            .filter(models.Question.topic != INTRO_QUESTION["topic"])
            .count()
        )
        if existing_non_intro > 0:
            print(f"[async plan] round {round_id} already has {existing_non_intro} planned questions — skipping")
            return

        interview = db.get(models.Interview, interview_id)
        if not interview:
            return
        questions = nodes.plan_questions(
            interview.resume_text, interview.jd_text, role, round_no=round_no
        )
        # If plan_questions already prepended a self-intro (round_no==1) drop it
        # because we already inserted the fixed INTRO_QUESTION synchronously.
        if has_intro and questions and questions[0].get("topic") == INTRO_QUESTION["topic"]:
            questions = questions[1:]
        # Extra dedup at insert time: skip any question whose exact text OR
        # (case-insensitive stripped) topic already exists in this round. LLMs
        # often paraphrase the same question so text-only dedup isn't enough.
        existing_rows = db.query(models.Question.question_text, models.Question.topic).filter_by(round_id=round_id).all()
        existing_texts = {r[0] for r in existing_rows}
        existing_topics = {(r[1] or "").strip().lower() for r in existing_rows if r[1]}
        base_seq = db.query(models.Question).filter_by(round_id=round_id).count()
        seq_cursor = base_seq
        for q in questions:
            if q["question"] in existing_texts:
                continue
            topic_key = (q["topic"] or "").strip().lower()
            if topic_key and topic_key in existing_topics:
                continue
            existing_texts.add(q["question"])
            if topic_key:
                existing_topics.add(topic_key)
            seq_cursor += 1
            db.add(models.Question(
                round_id=round_id, seq=seq_cursor,
                topic=q["topic"], question_text=q["question"],
            ))
        db.commit()
        _tts_all_async(round_id)
    except Exception as e:
        print(f"[async plan] error: {e}")
    finally:
        db.close()
        with _planning_lock:
            _planning_rounds.discard(round_id)


def _tts_question_async(question_id: int):
    db = SessionLocal()
    try:
        q = db.get(models.Question, question_id)
        if q and not q.tts_path:
            q.tts_path = voice_service.save_tts(q.question_text)
            db.commit()
    except Exception as e:
        print(f"[async tts] error: {e}")
    finally:
        db.close()


def _tts_all_async(round_id: int):
    db = SessionLocal()
    try:
        qs = db.query(models.Question).filter_by(round_id=round_id).order_by(models.Question.seq).all()
        for q in qs:
            if not q.tts_path:
                try:
                    q.tts_path = voice_service.save_tts(q.question_text)
                    db.commit()
                except Exception as e:
                    print(f"[async tts-all] error on q{q.id}: {e}")
    finally:
        db.close()


def _ensure_tts(q: models.Question, db: Session):
    if q and not q.tts_path:
        q.tts_path = voice_service.save_tts(q.question_text)
        db.commit()


def _question_to_out(q: models.Question) -> schemas.QuestionOut:
    return schemas.QuestionOut(
        id=q.id, seq=q.seq, topic=q.topic, question_text=q.question_text,
        is_followup=q.is_followup,
        tts_url=_tts_url(q.tts_path) if q.tts_path else None,
    )


# ---------- Answer + advance ----------

@router.post("/{interview_id}/answer")
def submit_answer(interview_id: int, body: schemas.AnswerIn, db: Session = Depends(get_db)):
    """Submit an answer -> score -> agent decides followup or next question -> return next question or round-end."""
    q = db.get(models.Question, body.question_id)
    if not q:
        raise HTTPException(404, "question not found")

    # Idempotency guard: if this question already has an answer, treat this
    # call as a retry / duplicate submission and DO NOT run score+followup
    # again (which would spawn another follow-up question and duplicate the
    # score). Return the current "next question" state instead.
    existing_answer = db.query(models.Answer).filter_by(question_id=q.id).first()
    if existing_answer:
        # Find whatever the frontend should show next: the earliest unanswered
        # question in the round (main or already-inserted followup).
        nxt = (
            db.query(models.Question)
            .outerjoin(models.Answer, models.Answer.question_id == models.Question.id)
            .filter(models.Question.round_id == q.round_id)
            .filter(models.Answer.id.is_(None))
            .order_by(models.Question.seq)
            .first()
        )
        result: dict = {"score": None, "decision": {"action": "next", "followup_question": "", "reason": "duplicate submit"}}
        if nxt:
            result["next_question"] = _question_to_out(nxt).model_dump()
        return result

    # save answer
    answer = models.Answer(
        question_id=q.id,
        transcript=body.transcript,
        audio_path=body.audio_path,
        duration_ms=body.duration_ms,
    )
    db.add(answer)
    db.flush()

    # Rule: user explicitly requested to end the interview.
    if is_end_signal(body.transcript):
        return _end_interview(q.round_id, db, ended_by_user=True)

    # --- A+B latency optimization ---
    # score_answer and decide_followup are independent LLM calls (~3-8s each).
    # score is only used later in the final report, so we (B) fire it into a
    # background thread that writes the Score row when done — the interview
    # flow does not wait for it.
    # decide_followup determines the next question so it MUST stay synchronous.
    # (A) is naturally achieved because score now runs concurrently in a
    # thread while we run decide_followup on the request thread.
    question_id_for_score = q.id
    question_text_for_score = q.question_text
    answer_text_for_score = body.transcript
    score_thread = threading.Thread(
        target=_score_and_save_async,
        args=(question_id_for_score, question_text_for_score, answer_text_for_score),
        daemon=True,
    )
    score_thread.start()
    _register_pending_score(q.round_id, score_thread)

    # count followups on same "topic chain"
    root_id = q.parent_question_id or q.id
    followups_so_far = db.query(models.Question).filter(
        models.Question.round_id == q.round_id,
        models.Question.parent_question_id == root_id,
    ).count()

    # ask agent for followup decision
    decision = nodes.decide_followup(
        topic=q.topic or q.question_text[:40],
        question=q.question_text,
        answer=body.transcript,
        followups_so_far=followups_so_far,
    )

    result: dict = {"score": None, "decision": decision}

    if decision["action"] == "followup" and decision["followup_question"]:
        # Budget: adding a followup increases total questions. To keep interview
        # length bounded, drop the LAST unanswered main question (if any) whenever
        # we insert a followup.
        last_unanswered_main = (
            db.query(models.Question)
            .outerjoin(models.Answer, models.Answer.question_id == models.Question.id)
            .filter(models.Question.round_id == q.round_id)
            .filter(models.Question.is_followup.is_(False))
            .filter(models.Answer.id.is_(None))
            .order_by(models.Question.seq.desc())
            .first()
        )
        if last_unanswered_main and last_unanswered_main.id != q.id:
            db.delete(last_unanswered_main)

        # insert a new followup question after current one
        max_seq = db.query(models.Question).filter_by(round_id=q.round_id).count()
        fq = models.Question(
            round_id=q.round_id,
            seq=max_seq + 1,
            topic=q.topic,
            question_text=decision["followup_question"],
            is_followup=True,
            parent_question_id=root_id,
        )
        db.add(fq)
        db.commit()
        db.refresh(fq)
        # TTS the followup in background so we don't block the response
        threading.Thread(target=_tts_question_async, args=(fq.id,), daemon=True).start()
        result["next_question"] = _question_to_out(fq).model_dump()
        return result

    # next planned (non-followup) question that hasn't been answered yet.
    # NOTE: we can't compare by seq, because a follow-up gets seq = max+1 and would
    # skip over earlier main questions. Instead, LEFT JOIN answers and pick the
    # earliest main question with no answer row.
    next_q = (
        db.query(models.Question)
        .outerjoin(models.Answer, models.Answer.question_id == models.Question.id)
        .filter(models.Question.round_id == q.round_id)
        .filter(models.Question.is_followup.is_(False))
        .filter(models.Answer.id.is_(None))
        .order_by(models.Question.seq)
        .first()
    )
    if next_q:
        db.commit()
        # Don't block: TTS is (in the common case) already pre-generated by the
        # background task. If not, kick it off async — the frontend can poll or the
        # audio will lazy-load once ready.
        if not next_q.tts_path:
            threading.Thread(target=_tts_question_async, args=(next_q.id,), daemon=True).start()
        result["next_question"] = _question_to_out(next_q).model_dump()
        return result

    # round finished
    round_ = db.get(models.Round, q.round_id)
    _finalize_round(round_, db)
    db.commit()
    result["round_finished"] = True
    result["round"] = schemas.RoundOut.model_validate(round_).model_dump()

    interview = db.get(models.Interview, round_.interview_id)
    if not round_.passed:
        interview.status = "failed"
    elif round_.round_no >= interview.rounds_planned:
        interview.status = "completed"
    else:
        interview.status = "round_finished"
    db.commit()
    # If the interview is over (completed or failed), auto-generate the report now.
    if interview.status in ("completed", "failed"):
        try:
            _generate_report_for(interview, db)
        except Exception as e:
            print(f"[warn] auto report generation failed: {e}")
    result["interview_status"] = interview.status
    return result


def _finalize_round(round_: models.Round, db: Session):
    # Ensure background score threads for this round have finished writing
    # their Score rows before we aggregate.
    _wait_pending_scores(round_.id, db)
    db.expire_all()
    qs = db.query(models.Question).filter_by(round_id=round_.id).all()
    scores = []
    qa_log_parts = []
    for qq in qs:
        if qq.score_:
            scores.append(qq.score_.total)
        ans = qq.answer.transcript if qq.answer else "(未作答)"
        sc = qq.score_.total if qq.score_ else 0
        qa_log_parts.append(f"- [{qq.seq}] {qq.question_text}\n  回答: {ans}\n  得分: {sc}")
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0
    round_.score = avg
    round_.passed = avg >= settings.ROUND_PASS_THRESHOLD
    round_.status = "passed" if round_.passed else "failed"
    round_.feedback = nodes.summarize_round(round_.role, "\n".join(qa_log_parts), avg)


def _end_interview(round_id: int, db: Session, ended_by_user: bool = False) -> dict:
    """Terminate the current round and the whole interview immediately.
    Also generates the final report inline so the report page loads instantly."""
    round_ = db.get(models.Round, round_id)
    _finalize_round(round_, db)
    interview = db.get(models.Interview, round_.interview_id)
    interview.status = "completed"
    db.commit()
    # generate report so the user sees it immediately on the report page
    try:
        _generate_report_for(interview, db)
    except Exception as e:
        # report generation failing shouldn't block the end flow; the report page
        # will fall back to on-demand generation.
        print(f"[warn] auto report generation failed: {e}")
    return {
        "round_finished": True,
        "ended_by_user": ended_by_user,
        "round": schemas.RoundOut.model_validate(round_).model_dump(),
        "interview_status": interview.status,
    }


def _generate_report_for(interview: models.Interview, db: Session) -> models.Report:
    rounds_log_parts = []
    qa_log_parts = []
    for r in sorted(interview.rounds, key=lambda x: x.round_no):
        rounds_log_parts.append(
            f"第{r.round_no}轮 ({r.role}) 得分 {r.score} {'通过' if r.passed else '未通过'}\n评语: {r.feedback}"
        )
        for q in sorted(r.questions, key=lambda x: x.seq):
            ans = q.answer.transcript if q.answer else "(未作答)"
            sc = q.score_.total if q.score_ else 0
            comment = q.score_.comment if q.score_ else ""
            qa_log_parts.append(
                f"[第{r.round_no}轮 · Q{q.seq}] {q.question_text}\n答: {ans}\n得分: {sc}  点评: {comment}"
            )
    md_text = nodes.final_report(
        position=interview.position_title,
        rounds_log="\n\n".join(rounds_log_parts),
        qa_log="\n\n".join(qa_log_parts),
    )
    pdf_rel = report_pdf.render_pdf(f"面试报告 - {interview.position_title}", md_text)

    report = interview.report
    if not report:
        report = models.Report(interview_id=interview.id, overall_comment_md=md_text, pdf_path=pdf_rel)
        db.add(report)
    else:
        report.overall_comment_md = md_text
        report.pdf_path = pdf_rel
    db.commit()
    return report


# ---------- Report ----------

@router.post("/{interview_id}/end")
def end_interview(interview_id: int, db: Session = Depends(get_db)):
    """Explicitly end an interview (e.g. user clicked '结束面试'). Finalizes the
    current running round with whatever answers exist and marks interview completed."""
    interview = db.get(models.Interview, interview_id)
    if not interview:
        raise HTTPException(404)
    running = next((r for r in interview.rounds if r.status == "running"), None)
    if running:
        return _end_interview(running.id, db, ended_by_user=True)
    interview.status = "completed"
    db.commit()
    return {"interview_status": interview.status, "ended_by_user": True}


@router.post("/{interview_id}/report")
def generate_report(interview_id: int, db: Session = Depends(get_db)):
    interview = db.get(models.Interview, interview_id)
    if not interview:
        raise HTTPException(404)
    report = _generate_report_for(interview, db)
    return {
        "markdown": report.overall_comment_md,
        "pdf_url": _tts_url(report.pdf_path),
    }


@router.get("/{interview_id}/questions/{question_id}")
def get_question(interview_id: int, question_id: int, db: Session = Depends(get_db)):
    """Lightweight poll endpoint used by the frontend to fetch a question's TTS URL
    once it's ready in the background."""
    q = db.get(models.Question, question_id)
    if not q:
        raise HTTPException(404)
    return _question_to_out(q).model_dump()


@router.get("/{interview_id}/detail")
def get_detail(interview_id: int, db: Session = Depends(get_db)):
    interview = db.get(models.Interview, interview_id)
    if not interview:
        raise HTTPException(404)
    rounds = []
    for r in sorted(interview.rounds, key=lambda x: x.round_no):
        qs = []
        for q in sorted(r.questions, key=lambda x: x.seq):
            qs.append({
                "id": q.id, "seq": q.seq, "topic": q.topic,
                "question_text": q.question_text, "is_followup": q.is_followup,
                "tts_url": _tts_url(q.tts_path) if q.tts_path else None,
                "answer": q.answer.transcript if q.answer else None,
                "answer_audio_url": (
                    _tts_url(q.answer.audio_path)
                    if (q.answer and q.answer.audio_path) else None
                ),
                "score": q.score_.total if q.score_ else None,
                "score_comment": q.score_.comment if q.score_ else None,
                "dimensions": q.score_.dimensions if q.score_ else None,
            })
        rounds.append({
            "id": r.id, "round_no": r.round_no, "role": r.role, "status": r.status,
            "score": r.score, "passed": r.passed, "feedback": r.feedback,
            "questions": qs,
        })
    return {
        "id": interview.id,
        "position_title": interview.position_title,
        "status": interview.status,
        "rounds_planned": interview.rounds_planned,
        "current_round_no": interview.current_round_no,
        "rounds": rounds,
        "report": {
            "markdown": interview.report.overall_comment_md if interview.report else None,
            "pdf_url": _tts_url(interview.report.pdf_path) if (interview.report and interview.report.pdf_path) else None,
        },
    }
