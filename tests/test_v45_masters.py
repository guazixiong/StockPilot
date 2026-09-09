"""v4.5：大师投委会（Augur 融合）单测——角色/解析/加权共识，无网络。"""
from stockpilot.core import masters as mc


def test_registry_and_selection():
    """注册表完整 + 按 key 选人 + 非法 key 忽略。"""
    assert len(mc.MASTERS) >= 12
    assert "段永平" == mc.MASTERS["duan"].name
    assert mc.MASTERS["trend"].school == "趋势"      # 独有席位
    sel = mc.masters_by_keys(["duan", "buffett", "nope"])
    assert [m.key for m in sel] == ["duan", "buffett"]
    assert len(mc.masters_by_keys()) == len(mc.MASTERS)


def test_parse_json_reply():
    """标准 JSON 输出解析（augur 式 schema）。"""
    r = mc.parse_master_reply(
        "商业模式清晰。\n{\"signal\": \"看多\", \"score\": 7, \"reason\": \"护城河宽价格合理\"}")
    assert r["signal"] == "看多" and r["score"] == 7.0
    assert "护城河" in r["reason"]


def test_parse_keyword_fallback():
    """散文式回复兜底（宽容解析，LLM 不守 schema 不报废）。"""
    r = mc.parse_master_reply("综合判断，这只票我选择看空，估值太贵。")
    assert r["signal"] == "看空" and r["score"] == -3.0
    r2 = mc.parse_master_reply("我看多。")
    assert r2["signal"] == "看多"
    r3 = mc.parse_master_reply("")
    assert r3["signal"] == "观望"


def test_parse_score_clamped():
    """score 越界钳制。"""
    r = mc.parse_master_reply('{"signal":"看多","score":999,"reason":"x"}')
    assert r["score"] == 10.0
    r2 = mc.parse_master_reply('{"signal":"看空","score":-88,"reason":"x"}')
    assert r2["score"] == -10.0


def test_consensus_bull_majority():
    """多数看多且加权分达标 → 看多裁决 + 空方分歧清单。"""
    masters = mc.masters_by_keys(["duan", "buffett", "trend", "dalio"])
    replies = {
        "duan": {"signal": "看多", "score": 8, "reason": "商业模式清晰"},
        "buffett": {"signal": "看多", "score": 6, "reason": "护城河真实"},
        "trend": {"signal": "看多", "score": 4, "reason": "均线多头"},
        "dalio": {"signal": "看空", "score": -5, "reason": "周期顶部"},
    }
    c = mc.compute_consensus(replies, masters)
    assert c.verdict == "看多"
    assert c.distribution == {"看多": 3, "观望": 0, "看空": 1}
    assert c.total == 4
    assert c.weighted_score > 0
    assert any("达利欧" in d for d in c.dissent)   # 空方意见进分歧清单
    assert "看多" in c.summary and "3多" in c.summary


def test_consensus_split_downgrades_watch():
    """五五开分歧 → 降级观望（augur 同思路：分歧大不硬判）。"""
    masters = mc.masters_by_keys(["duan", "graham", "fisher", "marks"])
    replies = {
        "duan": {"signal": "看多", "score": 9, "reason": "a"},
        "graham": {"signal": "看空", "score": -9, "reason": "b"},
        "fisher": {"signal": "看多", "score": 8, "reason": "c"},
        "marks": {"signal": "看空", "score": -8, "reason": "d"},
    }
    c = mc.compute_consensus(replies, masters)
    assert c.verdict == "观望"
    assert c.distribution["看多"] == 2 and c.distribution["看空"] == 2


def test_consensus_empty_and_quorum():
    """空回复 → 观望+无人发言；权重大师意见主导加权分。"""
    masters = mc.masters_by_keys(["duan", "trend"])
    c = mc.compute_consensus({}, masters)
    assert c.verdict == "观望" and c.total == 0 and "无可投票" in c.summary
    c2 = mc.compute_consensus(
        {"duan": {"signal": "看多", "score": 10, "reason": "x"},
         "trend": {"signal": "看空", "score": -2, "reason": "y"}}, masters)
    # 票数 1:1（恰好半数）→ 裁决看加权分：段永平 1.2×10 vs 趋势 1.0×-2
    # 加权分 4.5 ≥ 2 → 强信念胜出为"看多"；反之为"看空"；弱信念才观望
    assert c2.verdict == "看多"
    assert c2.weighted_score > 2
    # 但若加权分也弱（1:1 且信念接近），降级观望——分歧大不硬判
    c3 = mc.compute_consensus(
        {"duan": {"signal": "看多", "score": 2, "reason": "x"},
         "trend": {"signal": "看空", "score": -2, "reason": "y"}}, masters)
    assert c3.verdict == "观望"


class _MockClient:
    """逐师返回固定文本的假 client。"""

    def __init__(self):
        self.calls = []

    def chat(self, messages, stream=False, **kw):
        self.calls.append(messages)
        n = len(self.calls)
        sys_text = messages[0]["content"]
        if "段永平" in sys_text:
            return "好生意。\n{\"signal\": \"看多\", \"score\": 8, \"reason\": \"本分的好生意\"}"
        if "趋势" in sys_text:
            return "均线多头。\n{\"signal\": \"看多\", \"score\": 5, \"reason\": \"多头排列\"}"
        return "数据不足。\n{\"signal\": \"观望\", \"score\": 0, \"reason\": \"框架不适用\"}"


def test_run_committee_end_to_end():
    """mock 全链：角色提示词注入 → 逐师解析 → 加权共识。"""
    client = _MockClient()
    seen = []
    c = mc.run_committee(client, "个股数据包XYZ", keys=["duan", "trend", "buffett"],
                         on_master=lambda m, raw, p: seen.append((m.name, p["signal"])))
    assert [s[0] for s in seen] == ["段永平", "趋势派（量化）", "巴菲特"]
    assert [s[1] for s in seen] == ["看多", "看多", "观望"]
    assert c.verdict == "看多"
    assert "个股数据包XYZ" in client.calls[0][1]["content"]   # 数据包真的传入
    assert c.total == 3
    assert c.distribution == {"看多": 2, "观望": 1, "看空": 0}
