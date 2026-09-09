"""牛熊辩论模式（v4.4 G5 切片）：多角色对抗式 AI 个股研判。

借鉴 TradingAgents 的"多智能体对抗"理念做桌面轻量版：
  1. 多头分析师：只找买入证据（ bullish case）
  2. 空头分析师：只找卖出/回避证据（bearish case）
  3. 中性首席：双方陈词后给出最终裁决（多方/空方/观望 + 理由 + 反方风险）

纯函数编排：不发起网络请求，只构造 messages 序列，由 UI 层的
client.chat 逐轮消费并流式呈现（复用个股诊断的流式通道）。

用法：
    rounds = build_debate_rounds(context, rounds=3)
    for role, title, messages in rounds:
        reply = client.chat(messages, ...)
        feed(role, title, reply)     # UI 逐段追加
裁决轮为最后一轮（role="judge"），其 messages 会带上前几轮双方原文。
"""
from __future__ import annotations

from typing import List, Tuple

Round = Tuple[str, str, List[dict]]   # (role, 标题, messages)

BULL_SYS = (
    "你是一位 A 股多头分析师（bull analyst）。你的职责是站在买方立场，"
    "从数据中找出支持买入或继续持有的证据：趋势、量能、资金、形态、估值、"
    "催化剂。必须诚实：数据不支持时明说'多方案例薄弱'，不得编造利好。"
    "输出 ≤200 字，条目化列出 3~5 条证据，每条注明引用的数据。"
)

BEAR_SYS = (
    "你是一位 A 股空头分析师（bear analyst）。你的职责是站在卖方立场，"
    "从同样的数据中找出风险与卖出/回避的证据：破位、放量滞涨、估值泡沫、"
    "获利盘压力、利空催化。必须诚实：数据不支持时明说'空方案例薄弱'。"
    "输出 ≤200 字，条目化列出 3~5 条证据，每条注明引用的数据。"
)

JUDGE_SYS = (
    "你是一位中立的首席投资官。现在你收到了多头与空头分析师的陈词。"
    "请独立复核双方证据（以数据为准，不被话术带偏），输出裁决：\n"
    "① 结论：看多 / 看空 / 观望（三选一）\n"
    "② 三条核心理由\n"
    "③ 多头忽略了什么风险、空头忽略了什么利好\n"
    "④ 操作建议（若看多给买入区间/止损；若看空给回避条件；观望给触发条件）\n"
    "输出 ≤300 字。语气克制，只在数据支持时下结论。"
)


def build_debate_rounds(context: str, question: str = "",
                        rounds: int = 3) -> List[Round]:
    """构造辩论轮次。rounds=3 → 牛/熊/裁决（标准）；rounds=5 →
    牛/熊/牛反驳/熊反驳/裁决（对抗加深）。返回按执行顺序的轮次列表，
    每轮 (role, 标题, messages)；裁决轮自动携带此前所有轮次原文。"""
    rounds = max(3, min(int(rounds), 5))
    plan: List[Tuple[str, str, str]] = [("bull", "🐂 多头立论", BULL_SYS)]
    if rounds >= 4:
        plan.append(("bear", "🐻 空头立论", BEAR_SYS))
        plan.append(("bull2", "🐂 多头反驳", BULL_SYS))
        plan.append(("bear2", "🐻 空头反驳", BEAR_SYS))
    else:
        plan.append(("bear", "🐻 空头立论", BEAR_SYS))
    plan.append(("judge", "⚖️ 首席裁决", JUDGE_SYS))

    q = question.strip() or "（无补充问题）"
    transcript: List[dict] = []      # 双方对话记录（喂给裁决）
    out: List[Round] = []
    for i, (role, title, sys_text) in enumerate(plan):
        if role == "judge":
            msgs = [{"role": "system", "content": JUDGE_SYS}]
            msgs.extend(transcript)
            msgs.append({
                "role": "user",
                "content": "以下是双方完整陈词记录。请给出最终裁决。\n\n"
                           + _transcript_text(transcript, exclude_last=True)
                           + f"\n\n用户补充：{q}",
            })
        else:
            msgs = [{"role": "system", "content": sys_text}]
            # 反驳轮需看到对方上一轮陈词
            if "2" in role and transcript:
                msgs.append({
                    "role": "user",
                    "content": "对方观点如下，请针对性反驳（不重复自己，"
                               "直击对方论据的数据漏洞）：\n\n"
                               + transcript[-1]["content"][:800]
                               + f"\n\n原始数据与背景：\n\n{context[:2500]}"
                               + f"\n\n用户补充：{q}",
                })
            else:
                msgs.append({
                    "role": "user",
                    "content": "请基于以下个股数据给出你方陈词。\n\n"
                               + context + f"\n\n用户补充：{q}",
                })
        out.append((role, title, msgs))
        # 预登记非裁决轮的陈词占位（内容由调用方回填）
        transcript.append({"role": "user", "content": f"[{title}]"})
    return out


def feed_reply(rounds: List[Round], idx: int, reply: str) -> None:
    """把第 idx 轮的模型回复回填进后续（裁决）轮的对话记录。
    在原地修改 rounds 的后续轮 messages。"""
    if idx < 0 or idx >= len(rounds):
        return
    role, _title, _msgs = rounds[idx]
    text = f"[{_title}]\n{reply}"
    for j in range(idx + 1, len(rounds)):
        r2, _t2, msgs2 = rounds[j]
        if r2 == "judge":
            # 裁决轮在末尾 user 消息前插入双方陈词
            for m in msgs2:
                if m["role"] == "user" and m["content"].startswith("以下是双方"):
                    m["content"] = _transcript_so_far(rounds, idx, reply)
                    return
        elif "2" in r2:
            # 反驳轮的 user 消息引用对方上一轮 → 已是结构化文本，无需改
            continue


def _transcript_text(transcript: List[dict], exclude_last: bool = False) -> str:
    body = transcript[:-1] if exclude_last else transcript
    return "\n\n".join(
        f"{m['content']}" for m in body if not m["content"].startswith("[")
    ) or "（双方尚未陈词）"


def _transcript_so_far(rounds: List[Round], upto: int, last_reply: str) -> str:
    parts: List[str] = []
    for k in range(upto + 1):
        _r, title, _m = rounds[k]
        reply = last_reply if k == upto else None
        if reply is None:
            parts.append(f"{title}（未执行）")
        else:
            parts.append(f"{title}：\n{reply}")
    return "\n\n".join(parts)


def run_debate(client, context: str, question: str = "",
               rounds: int = 3, on_round=None) -> str:
    """同步执行整场辩论（供测试/无 UI 场景）。on_round(role, title, reply)
    每轮回调。返回裁决文本。"""
    plan = build_debate_rounds(context, question, rounds)
    replies: List[str] = []
    for i, (role, title, msgs) in enumerate(plan):
        # 裁决轮：重建携带真实陈词的消息
        if role == "judge":
            msgs = [{"role": "system", "content": JUDGE_SYS},
                    {"role": "user",
                     "content": "以下是双方完整陈词记录。请给出最终裁决。\n\n"
                                + "\n\n".join(
                                        f"{plan[k][1]}：\n{replies[k]}"
                                        for k in range(len(plan) - 1))
                                + f"\n\n用户补充：{question.strip() or '（无）'}"}]
        reply = client.chat(msgs, stream=False)
        replies.append(reply)
        if on_round:
            on_round(role, title, reply)
    return replies[-1]
