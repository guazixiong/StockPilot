"""策略引擎：规则 DSL + 策略模型 + 信号生成。

监听（core/monitor.py）与回测（core/backtest.py）共用同一套策略定义，
保证"回测验证的策略"与"实盘监听的策略"口径一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import KLine, Quote, TradeSignal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 规则模型

@dataclass
class Rule:
    indicator: str          # 指标/上下文字段名，见 indicators.analyze
    op: str                 # > < >= <= == between
    value: object           # 数值 / [a,b] / 另一个指标名(str) / bool
    logic: str = "and"      # and / or

    def to_dict(self) -> dict:
        return {"indicator": self.indicator, "op": self.op,
                "value": self.value, "logic": self.logic}

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(str(d.get("indicator", "")), str(d.get("op", ">")),
                   d.get("value"), str(d.get("logic", "and")))


@dataclass
class Strategy:
    name: str
    buy_rules: List[Rule] = field(default_factory=list)
    sell_rules: List[Rule] = field(default_factory=list)
    stop_loss_pct: float = 6.0
    take_profit_pct: float = 12.0
    max_hold_days: int = 20
    desc: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name,
                "buy_rules": [r.to_dict() for r in self.buy_rules],
                "sell_rules": [r.to_dict() for r in self.sell_rules],
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "max_hold_days": self.max_hold_days,
                "desc": self.desc}

    @classmethod
    def from_dict(cls, d: dict) -> "Strategy":
        return cls(
            name=str(d.get("name", "未命名")),
            buy_rules=[Rule.from_dict(x) for x in d.get("buy_rules") or []],
            sell_rules=[Rule.from_dict(x) for x in d.get("sell_rules") or []],
            stop_loss_pct=float(d.get("stop_loss_pct") or 6),
            take_profit_pct=float(d.get("take_profit_pct") or 12),
            max_hold_days=int(d.get("max_hold_days") or 20),
            desc=str(d.get("desc") or ""),
        )


# ---------------------------------------------------------------- 指标中文名（唯一权威映射）

INDICATOR_NAMES: Dict[str, str] = {
    # 行情快照
    "price": "现价", "open": "今开", "high": "最高", "low": "最低",
    "change_pct": "涨跌幅%", "prev_close": "昨收", "prev_low": "昨日最低",
    # 均线
    "ma5": "5日均线MA5", "ma10": "10日均线MA10", "ma20": "20日均线MA20",
    "ma30": "30日均线MA30", "ma60": "60日均线MA60", "ma120": "120日均线(半年线)",
    "ma250": "250日均线(年线)", "ema12": "12日EMA", "ema26": "26日EMA",
    "ma_bull": "均线多头排列", "ma_bear": "均线空头排列", "ma20_rising": "MA20向上",
    # MACD
    "dif": "MACD快线DIF", "dea": "MACD慢线DEA", "macd_hist": "MACD柱",
    "macd_golden": "MACD金叉", "macd_dead": "MACD死叉", "macd_bull": "MACD多头",
    # KDJ / RSI
    "k": "KDJ-K值", "d": "KDJ-D值", "j": "KDJ-J值", "kdj_golden": "KDJ金叉",
    "rsi6": "RSI6", "rsi12": "RSI12", "rsi24": "RSI24",
    # 动量 / 区间
    "roc12": "变动率ROC12", "psy12": "心理线PSY12",
    "boll_up": "布林上轨", "boll_mid": "布林中轨", "boll_low": "布林下轨",
    "bias6": "乖离率BIAS6", "bias12": "乖离率BIAS12", "bias24": "乖离率BIAS24",
    "wr14": "威廉WR14", "cci14": "CCI14",
    "dd_from_high20": "距20日高点回撤%",
    # DMI / OBV
    "plus_di": "DMI多头力(+DI)", "minus_di": "DMI空头力(-DI)",
    "adx": "趋势强度ADX", "dmi_golden": "+DI上穿-DI", "obv_rising": "OBV能量潮上升",
    # N日涨幅 / 连涨连跌
    "chg5d": "5日涨幅%", "chg10d": "10日涨幅%", "chg20d": "20日涨幅%",
    "chg60d": "60日涨幅%", "up_days": "连涨天数", "down_days": "连跌天数",
    # N日高低（前N日，不含当日）
    "high10_prev": "前10日最高", "high20_prev": "前20日最高",
    "high30_prev": "前30日最高", "high60_prev": "前60日最高",
    "low10_prev": "前10日最低", "low20_prev": "前20日最低",
    "low30_prev": "前30日最低", "low60_prev": "前60日最低",
    # 量价
    "volume_ratio": "量比", "turnover_rate": "换手率%",
    "vol_vs_ma5": "量能比(今量/5日均量)", "amt_ratio_5d": "成交额5日比",
    "amplitude": "振幅%", "amount": "成交额(万)",
    "main_inflow": "主力净流入(万)",   # v4.3 实时快照字段（回测无）
    # 基本面
    "pe": "市盈率PE", "pb": "市净率PB",
    # 筹码分布
    "cyq_profit": "筹码获利盘%", "cyq_avg_cost": "筹码平均成本",
    "cyq_near": "现价附近筹码%", "cyq_conc": "筹码集中度%",
    # Sequoia 形态策略上下文
    "is_yang": "实体阳线", "amp40": "40日振幅比", "amp10": "10日振幅比",
    "high10_hold": "10日低点未破40日高80%",
    "prev_ma5": "昨日MA5", "prev_ma20": "昨日MA20",
    "vol_vs_ma20": "量能比(今量/20日均量)", "vol_vs_prev": "量比(今量/昨量)",
    "prev_limit_up": "昨日涨停", "prev_limit_down": "昨日跌停",
    "is_bear_today": "今日收阴", "prev_ma20_gt_ma60": "昨日均线多头(MA20>MA60)",
    "prev_close": "昨收",
    "float_mv": "流通市值(亿)", "total_mv": "总市值(亿)",
}

_ZH_TO_EN = {v: k for k, v in INDICATOR_NAMES.items()}

OP_NAMES = {">": "高于", "<": "低于", ">=": "不低于", "<=": "不高于",
            "==": "等于", "between": "介于"}
_BOOL_TRUE = {"true", "True", "1", "是", "成立"}


def zh_name(key: str) -> str:
    """英文指标键 → 中文名（未知原样返回）。"""
    return INDICATOR_NAMES.get(key, key)


def en_key(name: str) -> Optional[str]:
    """中文名/英文键 → 英文键（未知返回 None）。"""
    return _ZH_TO_EN.get(name) or (name if name in INDICATOR_NAMES else None)



def _to_float(x) -> Optional[float]:
    if x is None or isinstance(x, bool) or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def eval_rule(rule: Rule, ctx: Dict[str, object]) -> bool:
    left = ctx.get(rule.indicator)
    if left is None:
        return False
    # 右值若为已知指标名（英文键或中文名）则取其值（实现"现价 < MA20"类规则）
    if isinstance(rule.value, str):
        vkey = en_key(rule.value)
        if vkey and vkey in ctx:
            right = ctx[vkey]
        else:
            right = rule.value
    else:
        right = rule.value

    if rule.op == "==":
        if isinstance(right, str):
            # 布尔规则的宽松中文值：true/是/成立 ↔ false/否/不成立
            lv = bool(left)
            if right in _BOOL_TRUE:
                return lv is True
            if right.lower() in ("false", "0", "否", "不成立"):
                return lv is False
            return False
        return bool(left) == bool(right)
    if rule.op == "between":
        vals = right if isinstance(right, (list, tuple)) else [right, right]
        lf = _to_float(left)
        lo = _to_float(vals[0] if len(vals) > 0 else None)
        hi = _to_float(vals[1] if len(vals) > 1 else None)
        if lf is None or lo is None or hi is None:
            return False
        return lo <= lf <= hi

    lf = _to_float(left)
    rf = _to_float(right)
    if lf is None or rf is None:
        return False
    return {
        ">": lf > rf, "<": lf < rf, ">=": lf >= rf, "<=": lf <= rf,
    }.get(rule.op, False)


def eval_rules(rules: List[Rule], ctx: Dict[str, object]) -> Tuple[bool, List[str]]:
    """and 规则须全部命中；存在 or 规则时任一命中即可（仍需满足全部 and）。"""
    and_rules = [r for r in rules if r.logic != "or"]
    or_rules = [r for r in rules if r.logic == "or"]
    hit_names: List[str] = []
    for r in and_rules:
        if eval_rule(r, ctx):
            hit_names.append(_rule_text(r))
        else:
            return False, []
    if or_rules:
        for r in or_rules:
            if eval_rule(r, ctx):
                hit_names.append(_rule_text(r))
                return True, hit_names
        return False, []
    return bool(and_rules), hit_names


def _rule_text(r: Rule) -> str:
    """规则文本中文化：'现价 高于 20日均线MA20' / 'MACD金叉' / 'RSI6 介于 30~70'。"""
    name = zh_name(r.indicator)
    val = r.value
    # 布尔指标简写：==true → 名称本身；==false → 非名称
    if r.op == "==":
        sv = str(val).strip().lower() if val is not None else ""
        if sv in ("true", "1", "是", "成立"):
            return name
        if sv in ("false", "0", "否", "不成立"):
            return f"非{name}"
    # 右值为另一指标名 → 中文名
    if isinstance(val, str):
        shown = zh_name(val) if en_key(val) else val
    elif isinstance(val, (list, tuple)):
        shown = f"{val[0]}~{val[1]}" if len(val) > 1 else str(val[0])
    else:
        shown = str(val)
    return f"{name} {OP_NAMES.get(r.op, r.op)} {shown}"


# ---------------------------------------------------------------- 风险评分

def risk_score(ind: Dict[str, object], quote: Quote) -> Tuple[float, List[str]]:
    """0~100，越低越稳。构成：偏离MA20、近期振幅、RSI超买、换手过热。"""
    notes: List[str] = []
    score = 0.0
    price = quote.price
    ma20 = ind.get("ma20")
    if price and isinstance(ma20, float) and ma20 > 0:
        dev = abs(price / ma20 - 1) * 100
        score += 0.35 * min(dev * 8, 100)
        if dev >= 8:
            notes.append(f"现价偏离MA20达{dev:.1f}%，警惕回调")
    highs = ind.get("_atr_hint")
    r6 = ind.get("rsi6")
    if isinstance(r6, float):
        if r6 > 70:
            score += 0.20 * min((r6 - 70) / 30 * 100, 100)
            notes.append(f"RSI6={r6:.0f} 超买")
        elif r6 < 25:
            notes.append(f"RSI6={r6:.0f} 深度超卖，波动可能加剧")
    tr = quote.turnover_rate
    if tr is not None:
        score += 0.20 * min(tr / 20 * 100, 100)
        if tr >= 15:
            notes.append(f"换手率{tr:.1f}%过热，注意筹码松动")
    amp = quote.amplitude
    if amp is not None:
        score += 0.25 * min(amp * 10, 100)
        if amp >= 8:
            notes.append(f"日内振幅{amp:.1f}%，波动较大")
    if quote.pe is not None and quote.pe < 0:
        notes.append("净利润为负（PE<0），基本面风险")
        score = min(score + 10, 100)
    return round(min(score, 100.0), 1), notes


# ---------------------------------------------------------------- 信号生成

def build_ctx(ind: Dict[str, object], quote: Quote) -> Dict[str, object]:
    ctx = dict(ind)
    ctx["price"] = quote.price
    ctx["change_pct"] = quote.change_pct
    ctx["turnover_rate"] = quote.turnover_rate
    ctx["volume_ratio"] = quote.volume_ratio
    # 新浪备用源无量比：用 日成交量/5日均量 等效兜底（口径一致）
    if ctx["volume_ratio"] is None and ctx.get("vol_vs_ma5") is not None:
        ctx["volume_ratio"] = ctx.get("vol_vs_ma5")
    ctx["low"] = quote.low
    ctx["high"] = quote.high
    ctx["open"] = quote.open
    ctx["amplitude"] = quote.amplitude
    ctx["pe"] = quote.pe
    ctx["pb"] = quote.pb
    ctx["amount"] = quote.amount          # 成交额(万元)
    ctx["main_inflow"] = quote.main_inflow   # 主力资金净流入(万元) v4.3
    ctx["float_mv"] = quote.float_mv
    ctx["total_mv"] = quote.total_mv
    return ctx


def check_buy(strategy: Strategy, quote: Quote, ind: Dict[str, object],
              now: str = "") -> Optional[TradeSignal]:
    ctx = build_ctx(ind, quote)
    hit, hit_rules = eval_rules(strategy.buy_rules, ctx)
    if not hit or not quote.price:
        return None
    score, notes = risk_score(ind, quote)
    stop = quote.price * (1 - strategy.stop_loss_pct / 100)
    ma20 = ind.get("ma20")
    if isinstance(ma20, float) and ma20 > 0:
        stop = max(stop, ma20 * 0.99)
    target = quote.price * (1 + strategy.take_profit_pct / 100)
    return TradeSignal(
        time=now, code=quote.code, name=quote.name or quote.code,
        strategy=strategy.name, side="buy", price=quote.price,
        stop_price=round(stop, 2), target_price=round(target, 2),
        risk_score=score, hit_rules=hit_rules, risk_notes=notes,
        reason=f"命中策略[{strategy.name}]：{'；'.join(hit_rules)}",
    )


def check_sell(strategy: Strategy, quote: Quote, ind: Dict[str, object],
               now: str = "") -> Optional[TradeSignal]:
    ctx = build_ctx(ind, quote)
    rules = list(strategy.sell_rules)
    if quote.price and isinstance(ctx.get("ma10"), float):
        pass  # 卖出规则由策略定义，默认规则在 MonitorService 中补充风控线
    if not rules:
        return None
    hit, hit_rules = eval_rules(rules, ctx)
    if not hit:
        return None
    score, notes = risk_score(ind, quote)
    return TradeSignal(
        time=now, code=quote.code, name=quote.name or quote.code,
        strategy=strategy.name, side="sell", price=quote.price,
        risk_score=score, hit_rules=hit_rules, risk_notes=notes,
        reason=f"触发卖出条件[{strategy.name}]：{'；'.join(hit_rules)}",
    )


# ---------------------------------------------------------------- 内置策略

def builtin_strategies() -> List[Strategy]:
    return [
        Strategy(
            name="趋势启动",
            desc="均线多头+MACD金叉，顺势买入",
            buy_rules=[
                Rule("ma_bull", "==", True),
                Rule("macd_golden", "==", True),
                Rule("change_pct", "between", [0, 6]),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=6, take_profit_pct=12, max_hold_days=20,
        ),
        Strategy(
            name="放量突破",
            desc="创20日新高且放量，突破追入",
            buy_rules=[
                Rule("price", ">=", "high20_prev"),
                Rule("volume_ratio", ">", 1.5),
                Rule("turnover_rate", "between", [3, 15]),
            ],
            sell_rules=[Rule("price", "<", "ma10")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=15,
        ),
        Strategy(
            name="回调企稳",
            desc="回踩上升MA20缩量企稳，低吸",
            buy_rules=[
                Rule("low", "<=", "ma20"),
                Rule("price", ">", "ma20"),
                Rule("ma20_rising", "==", True),
                Rule("volume_ratio", "<", 1.2),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=20,
        ),
        Strategy(
            name="超卖反弹",
            desc="RSI超卖后企稳反抽，博反弹",
            buy_rules=[
                Rule("rsi6", "<", 30),
                Rule("price", ">", "prev_low"),
            ],
            sell_rules=[Rule("rsi6", ">", 70)],
            stop_loss_pct=5, take_profit_pct=8, max_hold_days=10,
        ),
        Strategy(
            name="强势回调",
            desc="中长期上升趋势中回踩10日线，低吸",
            buy_rules=[
                Rule("price", ">", "ma60"),
                Rule("low", "<=", "ma10"),
                Rule("price", ">", "ma10"),
                Rule("change_pct", "between", [-4, 4]),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=15,
        ),
        Strategy(
            name="布林下轨反弹",
            desc="触及布林下轨后放量收复，博修复",
            buy_rules=[
                Rule("low", "<=", "boll_low"),
                Rule("price", ">", "boll_low"),
                Rule("vol_vs_ma5", ">", 1.0),
            ],
            sell_rules=[Rule("price", ">", "boll_up")],
            stop_loss_pct=5, take_profit_pct=9, max_hold_days=12,
        ),
        Strategy(
            name="平台突破",
            desc="放量突破30日横盘平台",
            buy_rules=[
                Rule("price", ">=", "high30_prev"),
                Rule("turnover_rate", "between", [3, 15]),
                Rule("volume_ratio", ">", 1.2),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=18,
        ),
        Strategy(
            name="均线粘合发散",
            desc="多均线收敛后首日放量上攻，博方向选择",
            buy_rules=[
                Rule("change_pct", ">", 2.5),
                Rule("vol_vs_ma5", ">", 2.0),
                Rule("price", ">", "ma5"),
            ],
            sell_rules=[Rule("price", "<", "ma10")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=15,
        ),
        Strategy(
            name="强势新高",
            desc="放量创60日新高，趋势加速段",
            buy_rules=[
                Rule("price", ">=", "high60_prev"),
                Rule("volume_ratio", ">", 1.5),
                Rule("chg20d", ">", 5),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=6, take_profit_pct=15, max_hold_days=20,
        ),
        Strategy(
            name="缩量回踩60日线",
            desc="上升趋势中缩量回踩半年支撑，低吸",
            buy_rules=[
                Rule("low", "<=", "ma60"),
                Rule("price", ">", "ma60"),
                Rule("vol_vs_ma5", "<", 1.0),
                Rule("chg60d", ">", 10),
            ],
            sell_rules=[Rule("price", ">", "boll_up")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=20,
        ),
        Strategy(
            name="KDJ超卖金叉",
            desc="KDJ超卖区金叉，短线博反弹",
            buy_rules=[
                Rule("kdj_golden", "==", True),
                Rule("j", "<", 30),
                Rule("bias6", "<", -2),
            ],
            sell_rules=[Rule("rsi6", ">", 65)],
            stop_loss_pct=4, take_profit_pct=8, max_hold_days=8,
        ),
        Strategy(
            name="温和放量上行",
            desc="温和放量+小阳连涨，趋势爬坡段",
            buy_rules=[
                Rule("up_days", ">=", 3),
                Rule("vol_vs_ma5", "between", [1.0, 2.0]),
                Rule("price", ">", "ma20"),
                Rule("adx", ">", 20),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=20,
        ),
        Strategy(
            name="低位密集突破",
            desc="筹码低位高度密集后放量上穿成本区，博主升",
            buy_rules=[
                Rule("cyq_conc", "<", 12),
                Rule("cyq_profit", "between", [40, 85]),
                Rule("price", ">", "cyq_avg_cost"),
                Rule("volume_ratio", ">", 1.5),
            ],
            sell_rules=[Rule("price", "<", "ma10")],
            stop_loss_pct=6, take_profit_pct=15, max_hold_days=25,
        ),
        Strategy(
            name="获利盘洗盘企稳",
            desc="高获利盘回踩平均成本企稳，趋势中洗盘低吸",
            buy_rules=[
                Rule("cyq_profit", ">", 70),
                Rule("low", "<=", "cyq_avg_cost"),
                Rule("price", ">", "cyq_avg_cost"),
                Rule("ma_bull", "==", True),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=20,
        ),
        Strategy(
            name="海龟20日突破",
            desc="突破20日新高+成交额过亿+阳线防诱多（Sequoia Turtle）",
            buy_rules=[
                Rule("price", ">", "high20_prev"),
                Rule("amount", ">", 10000),          # 快照成交额(万)>1亿
                Rule("is_yang", "==", True),
                Rule("price", ">", "prev_close"),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=6, take_profit_pct=15, max_hold_days=25,
        ),
        Strategy(
            name="均线金叉放量",
            desc="MA5上穿MA20+量超20日均量1.5倍（Sequoia MaVolume）",
            buy_rules=[
                Rule("ma5", ">", "ma20"),
                Rule("prev_ma5", "<", "prev_ma20"),
                Rule("vol_vs_ma20", ">", 1.5),
            ],
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=20,
        ),
        Strategy(
            name="高窄旗形突破",
            desc="40日涨60%后极度收敛缩量（Sequoia HighTightFlag）",
            buy_rules=[
                Rule("amp40", ">", 1.6),
                Rule("amp10", "<", 1.15),
                Rule("high10_hold", "==", True),
                Rule("vol_vs_ma20", "<", 0.6),
            ],
            sell_rules=[Rule("price", "<", "ma10")],
            stop_loss_pct=7, take_profit_pct=18, max_hold_days=30,
        ),
        Strategy(
            name="涨停洗盘",
            desc="昨日涨停今放量收阴不破昨收（Sequoia LimitUpShakeout）",
            buy_rules=[
                Rule("prev_limit_up", "==", True),
                Rule("is_bear_today", "==", True),
                Rule("vol_vs_prev", ">", 2.0),
                Rule("low", ">=", "prev_close"),
            ],
            sell_rules=[Rule("price", "<", "ma10")],
            stop_loss_pct=5, take_profit_pct=10, max_hold_days=10,
        ),
        Strategy(
            name="趋势跌停反包",
            desc="多头排列中放量跌停博错杀（Sequoia UptrendLimitDown）",
            buy_rules=[
                Rule("prev_ma20_gt_ma60", "==", True),
                Rule("price", "<=", 0.905),
                Rule("vol_vs_ma20", ">", 2.0),
            ],
            sell_rules=[Rule("price", ">", "ma20")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=15,
        ),
        Strategy(
            name="RPS强度突破",
            desc="120日涨幅全市场排名前10%+贴近高点（横截面专用，Sequoia RPS）",
            buy_rules=[Rule("price", ">", "prev_close")],   # 占位；真实由雷达 RPS 通道执行
            sell_rules=[Rule("price", "<", "ma20")],
            stop_loss_pct=6, take_profit_pct=15, max_hold_days=30,
        ),
        Strategy(
            name="BOLL收口突破",
            desc="布林带极度收口后向上突破，博波动扩张",
            buy_rules=[
                Rule("price", ">", "boll_mid"),
                Rule("boll_up", "<", "high20_prev"),
                Rule("vol_vs_ma5", ">", 1.5),
            ],
            sell_rules=[Rule("price", ">", "boll_up")],
            stop_loss_pct=5, take_profit_pct=12, max_hold_days=15,
        ),
    ]
