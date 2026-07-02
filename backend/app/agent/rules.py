"""Non-LLM control-flow rules for the interviewer agent.

These live outside prompts because they are deterministic, cheap, and testable.
Keep prompt-side rules in prompts.py — this file is only for code-side rules.
"""

# ---- End-of-interview keywords (case-insensitive substring match) ----
END_KEYWORDS = [
    "结束面试",
    "结束这次面试",
    "面试结束",
    "面试到此结束",
    "我想结束",
    "不想继续了",
    "到此为止",
    "end interview",
    "stop interview",
]


def is_end_signal(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(k.lower() in t for k in END_KEYWORDS)


# ---- Fixed opening question for the very first round ----
INTRO_QUESTION = {
    "topic": "自我介绍",
    "question": (
        "在正式开始之前，请先花 2-3 分钟做个自我介绍："
        "包含你的技术栈、最近做过的一到两个项目，以及为什么对这个岗位感兴趣。"
    ),
}
