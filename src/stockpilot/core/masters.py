"""大师投委会（v4.5，融合 Augur 多智能体共识机制）。

借鉴 BruceLanLan/augur（391★，18 位投资大师加权共识）的三个核心设计：
1. **角色知识档案**：每位大师有独立的投资哲学/筛选标准/行业偏好提示词，
   分析同一只票各自下结论（不是一个模型包揽全部视角）；
2. **结构化输出**：每位大师回复 JSON（signal/score/reason），
   解析失败降级为"中性"不计入极端票；
3. **加权共识**：非等权——按流派与个股上下文调权，聚合出
   多空分布 + 加权裁决 + 分歧清单（谁反对、为什么）。

与既有 🐂熊牛熊辩论的差异：辩论=对抗找盲点（2 角色无权重）；
投委会=多元共识（N 角色独立打分加权聚合）。两者互补。

纯函数编排：不发起网络请求；由 UI 层 client.chat 逐师消费（流式）。
A股语境适配：大师档案用中文重写，指标上下文来自 build_stock_context
（K线/资金/龙虎榜），不依赖 SEC EDGAR。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class Master:
    """一位投资大师角色。"""
    key: str
    name: str                    # 显示名
    school: str                  # 流派：价值/成长/宏观/中国价值/趋势
    weight: float = 1.0          # 基础权重（共识时归一化）
    style: str = ""              # 一句话风格画像（进 system 提示词）
    focus: List[str] = field(default_factory=list)   # 分析侧重维度


# ---------------------------------------------------------------- 角色注册表
# 档案浓缩自 augur 的 persona 知识库（中文大师为主，贴合 A 股语境）
# + 一位独有的"趋势技术派"（StockPilot 的量化基因，Augur 无此流派）
MASTERS: Dict[str, Master] = {
    "duan": Master(
        key="duan", name="段永平", school="中国价值", weight=1.2,
        style="本分哲学：只投商业模式能一句话讲清、护城河真实、管理层本分的企业；"
              "10 年视角，不懂不碰，看准重仓",
        focus=["商业模式", "护城河", "管理层", "长期前景"]),
    "zhang_lei": Master(
        key="zhang_lei", name="张磊（高瓴）", school="中国价值", weight=1.1,
        style="时间的朋友：重仓赛道龙头，找'疯狂创造长期价值'的企业；"
              "重视研发与格局，哑铃配置（科技+消费）",
        focus=["赛道格局", "研发投入", "成长空间", "行业地位"]),
    "li_lu": Master(
        key="li_lu", name="李录（喜马拉雅）", school="中国价值", weight=1.1,
        style="查理·芒格传人：能力圈原则，深度研究少数公司，"
              "买入前问'十年后这家公司价值多少'",
        focus=["能力圈", "内在价值", "管理层", "安全边际"]),
    "dan_bin": Master(
        key="dan_bin", name="但斌（东方港湾）", school="中国价值", weight=1.0,
        style="时间的玫瑰：聚焦世界级品牌与龙头，穿越周期的长期持有，"
              "重视品牌定价权",
        focus=["品牌", "定价权", "周期位置", "稀缺性"]),
    "buffett": Master(
        key="buffett", name="巴菲特", school="价值", weight=1.2,
        style="护城河+安全边际：ROE>15%、低负债、毛利率>40%、PE<25 才进入射程；"
              "别人贪婪我恐惧",
        focus=["护城河", "ROE/毛利率", "负债率", "估值"]),
    "graham": Master(
        key="graham", name="格雷厄姆", school="价值", weight=1.0,
        style="捡烟蒂与净流动资产：极度低估+统计分散，不追成长故事",
        focus=["低估程度", "净资产负债", "股息", "下行保护"]),
    "munger": Master(
        key="munger", name="芒格", school="价值", weight=1.1,
        style="多元思维模型：反过来想；用合理价格买伟大公司，回避'太难'的题目",
        focus=["思维模型核查", "商业模式", "心理误判", "估值"]),
    "fisher": Master(
        key="fisher", name="费雪", school="成长", weight=1.0,
        style="闲聊法：重研发/销售组织/利润率提升，找到能成长 15 年的少数卓越企业",
        focus=["研发", "销售组织", "利润率趋势", "成长持续性"]),
    "lynch": Master(
        key="lynch", name="彼得·林奇", school="成长", weight=1.0,
        style="六类公司分类法：缓慢增长/稳定/快速增长/周期/ turnaround/资产；"
              "PEG 视角，看得见的故事才买",
        focus=["增长类型", "PEG", "扩张空间", "催化剂"]),
    "cathie_wood": Master(
        key="cathie_wood", name="Cathie Wood", school="成长", weight=0.9,
        style="颠覆式创新：AI/基因/储能/区块链五年五倍叙事，高波动高信念",
        focus=["颠覆创新", "技术趋势", "弹性空间", "事件催化"]),
    "dalio": Master(
        key="dalio", name="瑞·达利欧", school="宏观", weight=1.0,
        style="债务周期与全天候：增长与通胀四象限定位，不押注单一周期",
        focus=["周期位置", "信贷与流动性", "板块轮动", "风险平衡"]),
    "marks": Master(
        key="marks", name="霍华德·马克斯", school="宏观", weight=1.0,
        style="钟摆与第二层思维：市场温度计比标的重要，'低风险高收益'来自买在悲观",
        focus=["市场情绪", "钟摆位置", "风险补偿", "周期判断"]),
    "trend": Master(
        key="trend", name="趋势派（量化）", school="趋势", weight=1.0,
        style="StockPilot 独有席位：只看数据——均线结构/量价/动量/资金流，"
              "不带叙事，破位即离场",
        focus=["均线结构", "量价配合", "动量", "主力资金"]),
}


def masters_by_keys(keys: Optional[List[str]] = None) -> List[Master]:
    """按 key 选大师（保持注册顺序）；None=全部；非法 key 忽略。"""
    if not keys:
        return list(MASTERS.values())
    return [MASTERS[k] for k in keys if k in MASTERS]


# ---------------------------------------------------------------- 结构化解析
_SIGNAL_MAP = {"看多": "看多", "bullish": "看多", "buy": "看多", "1": "看多",
               "看空": "看空", "bearish": "看空", "sell": "看空", "-1": "看空",
               "观望": "观望", "neutral": "观望", "hold": "观望", "0": "观望"}


def parse_master_reply(text: str) -> Dict:
    """解析一位大师的回复 → {signal, score, reason}。

    Augur 用严格 JSON schema；桌面场景用**宽容解析**（JSON 优先，
    关键词兜底）——LLM 偶发散文式回复不至整轮报废。
    score: -10..+10（看多力度），解析失败按 signal 给保守缺省。
    """
    out = {"signal": "观望", "score": 0.0, "reason": ""}
    if not text:
        return out
    # 1) 尝试 JSON（代码块内或裸）
    m = re.search(r"\{[^{}]*\"signal\"[^{}]*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            sig = _SIGNAL_MAP.get(str(obj.get("signal", "")).strip().lower())
            if sig:
                out["signal"] = sig
                try:
                    sc = float(obj.get("score", 0))
                    out["score"] = max(-10.0, min(10.0, sc))
                except (TypeError, ValueError):
                    pass
                out["reason"] = str(obj.get("reason", ""))[:300]
                return out
    # 2) 关键词兜底（宽容模式）
    low = text.lower()
    for kw, sig in (("看空", "看空"), ("bearish", "看空"), ("sell", "看空"),
                    ("看多", "看多"), ("bullish", "看多"), ("buy", "看多")):
        if kw in low:
            out["signal"] = sig
            out["score"] = 3.0 if sig == "看多" else -3.0
            out["reason"] = re.sub(r"\s+", " ", text)[:300]
            return out
    out["reason"] = re.sub(r"\s+", " ", text)[:300]
    return out


# ---------------------------------------------------------------- 共识引擎
@dataclass
class Consensus:
    verdict: str                    # 看多 / 看空 / 观望
    weighted_score: float           # -10..+10
    distribution: Dict[str, int]    # 多/观望/空 计数
    total: int
    dissent: List[str]              # 分歧清单（少数派意见）
    summary: str                    # 一句话裁决
    quotes: List[Dict] = field(default_factory=list)  # 每位大师结论


def compute_consensus(replies: Dict[str, Dict],
                      masters: List[Master]) -> Consensus:
    """加权共识（借鉴 augur ConsensusEngine，去掉其行业矩阵/学习权重的
    服务端依赖，保留核心）：加权均分 + 少数派 dissent + 分布 + 裁决。

    replies: {master_key: parse_master_reply 输出}
    masters: 参与本轮的 Master 列表（权重来源）
    """
    quotes = []
    wsum = 0.0
    wscore = 0.0
    dist = {"看多": 0, "观望": 0, "看空": 0}
    for m in masters:
        r = replies.get(m.key)
        if not r or not r.get("signal"):
            continue
        sig = r["signal"]
        if sig not in dist:
            continue
        dist[sig] += 1
        wsum += m.weight
        wscore += m.weight * (r.get("score") or 0.0)
        quotes.append({"master": m.name, "school": m.school,
                       "signal": sig, "score": r.get("score") or 0.0,
                       "reason": r.get("reason", "")})
    total = dist["看多"] + dist["观望"] + dist["看空"]
    avg = round(wscore / wsum, 2) if wsum > 0 else 0.0
    if total == 0:
        return Consensus("观望", 0.0, dist, 0, "无人发言", "（无可投票大师）", [])
    # 裁决：加权分为主，多数票为辅——分歧大时降级观望（augur 同思路）
    if avg >= 2.0 and dist["看多"] * 2 > total:
        verdict = "看多"
    elif avg <= -2.0 and dist["看空"] * 2 > total:
        verdict = "看空"
    elif dist["看多"] * 2 == total or dist["看空"] * 2 == total:
        verdict = "观望" if abs(avg) < 2.0 else ("看多" if avg > 0 else "看空")
    else:
        verdict = "观望"
    # 分歧清单：与裁决不同向的大师意见（少数派）
    opposite = {"看多": "看空", "看空": "看多"}
    dissent = [f"{q['master']}（{q['school']}）{q['signal']}：{q['reason'][:60]}"
               for q in quotes
               if q["signal"] in opposite.get(verdict, "")]
    dist_txt = f"{dist['看多']}多 / {dist['观望']}观望 / {dist['看空']}空"
    summary = (f"投委会加权 {avg:+.1f} 分 → **{verdict}**（{dist_txt}）")
    return Consensus(verdict, avg, dist, total, dissent, summary, quotes)


def build_master_prompt(master: Master, context: str,
                        question: str = "") -> List[dict]:
    """一位大师的 messages（角色 system + 个股数据 user）。"""
    sys_text = (
        f"你是{master.name}，投资流派：{master.school}。保持你的真实风格与原则。\n"
        f"风格画像：{master.style}\n"
        f"你重点关注：{'、'.join(master.focus)}。\n\n"
        "必须诚实：数据不支持你流派的判断时，直说'以我的框架这只票不适用/看不出机会'，"
        "不得为了观点多样而编造论据。\n\n"
        "分析后**最后一行**必须输出（严格遵守）：\n"
        "{\"signal\": \"看多|观望|看空\", \"score\": -10到10的整数, "
        "\"reason\": \"一句话核心理由（≤40字）\"}\n"
        "score 含义：你对它的信念强度（看多正/看空负/观望近 0）。"
    )
    user = (f"以下是我的个股数据包（K线/指标/资金/龙虎榜），"
            f"请以你的框架分析：\n\n{context}")
    if question:
        user += f"\n\n用户特别问题：{question}"
    return [{"role": "system", "content": sys_text},
            {"role": "user", "content": user}]


def run_committee(client, context: str, keys: Optional[List[str]] = None,
                  question: str = "", on_master=None) -> Consensus:
    """同步执行整场投委会（测试/无 UI 场景）。on_master(master, reply, parsed)。

    逐师串行（用户可接受多轮等待；并行版由 UI Worker 消费）。
    解析失败的大师自动降级观望且理由标注（不致整轮报废）。
    """
    masters = masters_by_keys(keys)
    replies: Dict[str, Dict] = {}
    for m in masters:
        try:
            raw = client.chat(build_master_prompt(m, context, question),
                              stream=False)
        except Exception as exc:  # noqa: BLE001 单师失败不拖垮委员会
            log.warning("大师 %s 分析失败: %s", m.name, exc)
            raw = ""
        parsed = parse_master_reply(raw)
        if not raw:
            parsed["reason"] = "（该大师本轮未出席/调用失败）"
        replies[m.key] = parsed
        if on_master:
            on_master(m, raw, parsed)
    return compute_consensus(replies, masters)
