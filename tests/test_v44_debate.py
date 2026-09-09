"""v4.4：牛熊辩论（G5 切片）编排逻辑单测（无网络，mock client）。"""
from stockpilot.core import debate


class _MockClient:
    """按轮次返回固定文本的假 client（chat(messages, stream=False)）。"""

    def __init__(self):
        self.calls: list = []

    def chat(self, messages, stream=False, **kw):
        self.calls.append([dict(m) for m in messages])
        n = len(self.calls)
        if n == 1:
            return "多头证据：MA多头排列；量比1.8温和放量。"
        if n == 2:
            return "空头证据：RSI6 超 85 短线超买；60日涨幅已 38%。"
        return "裁决：观望。多头趋势成立但短线超买。"


def test_build_debate_rounds_structure_3():
    """3 轮标准：牛/熊/裁决，顺序与角色正确。"""
    rounds = debate.build_debate_rounds("背景数据X", rounds=3)
    roles = [r[0] for r in rounds]
    assert roles == ["bull", "bear", "judge"]
    titles = [r[1] for r in rounds]
    assert "多头" in titles[0] and "空头" in titles[1] and "裁决" in titles[2]
    # 立论轮携带完整背景
    assert "背景数据X" in rounds[0][2][1]["content"]
    assert "背景数据X" in rounds[1][2][1]["content"]
    # 各轮 system 提示词不同
    assert rounds[0][2][0]["content"] == debate.BULL_SYS
    assert rounds[1][2][0]["content"] == debate.BEAR_SYS
    assert rounds[2][2][0]["content"] == debate.JUDGE_SYS


def test_build_debate_rounds_structure_5():
    """5 轮对抗：牛/熊/牛反驳/熊反驳/裁决；反驳轮引用对方上一轮。"""
    rounds = debate.build_debate_rounds("背景Y", rounds=5)
    roles = [r[0] for r in rounds]
    assert roles == ["bull", "bear", "bull2", "bear2", "judge"]
    # 反驳轮（第3轮=bull2）的 user 内容要求"反驳"
    assert "反驳" in rounds[2][2][1]["content"]


def test_rounds_clamped():
    """轮次钳制：0→3，99→5。"""
    assert len(debate.build_debate_rounds("c", rounds=0)) == 3
    assert len(debate.build_debate_rounds("c", rounds=99)) == 5


def test_run_debate_mock_end_to_end():
    """mock 三轮：每轮 system 正确、裁决轮带双方陈词原文，返回裁决文本。"""
    client = _MockClient()
    seen = []
    final = debate.run_debate(
        client, "背景Z", question="短期还能买吗？", rounds=3,
        on_round=lambda r, t, x: seen.append((r, x)))
    assert final.startswith("裁决")
    assert [s[0] for s in seen] == ["bull", "bear", "judge"]
    # 裁判轮收到双方原文
    judge_msgs = client.calls[-1]
    judge_user = next(m["content"] for m in judge_msgs if m["role"] == "user")
    assert "多头证据" in judge_user and "空头证据" in judge_user
    assert "短期还能买吗" in judge_user
    # 立论轮收到了问题与背景
    assert "背景Z" in client.calls[0][1]["content"]
