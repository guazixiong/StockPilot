"""数据模型：行情快照 / K线 / 资讯条目。字段允许为 None（外部接口防御式解析）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


def _f(x) -> Optional[float]:
    try:
        if x is None or x == "" or x == "-":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class Quote:
    """一只证券的实时报价快照。字段允许为 None（外部接口防御式解析）。"""

    code: str
    name: str = ""
    price: Optional[float] = None
    prev_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[float] = None        # 手
    amount: Optional[float] = None        # 万元
    turnover_rate: Optional[float] = None  # %
    volume_ratio: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    amplitude: Optional[float] = None
    float_mv: Optional[float] = None      # 亿元（流通）
    total_mv: Optional[float] = None      # 亿元（总）
    time: str = ""
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None
    # 五档盘口：价格与量(手)
    bid1_price: Optional[float] = None
    bid1_vol: Optional[float] = None
    bid2_price: Optional[float] = None
    bid2_vol: Optional[float] = None
    bid3_price: Optional[float] = None
    bid3_vol: Optional[float] = None
    bid4_price: Optional[float] = None
    bid4_vol: Optional[float] = None
    bid5_price: Optional[float] = None
    bid5_vol: Optional[float] = None
    ask1_price: Optional[float] = None
    ask1_vol: Optional[float] = None
    ask2_price: Optional[float] = None
    ask2_vol: Optional[float] = None
    ask3_price: Optional[float] = None
    ask3_vol: Optional[float] = None
    ask4_price: Optional[float] = None
    ask4_vol: Optional[float] = None
    ask5_price: Optional[float] = None
    ask5_vol: Optional[float] = None
    main_inflow: Optional[float] = None   # 主力资金净流入（万元）v4.3

    @classmethod
    def from_mapping(cls, code: str, m: dict) -> "Quote":
        fields = dict(
            code=code,
            name=m.get("name") or "",
            price=_f(m.get("price")),
            prev_close=_f(m.get("prev_close")),
            open=_f(m.get("open")),
            high=_f(m.get("high")),
            low=_f(m.get("low")),
            change=_f(m.get("change")),
            change_pct=_f(m.get("change_pct")),
            volume=_f(m.get("volume")),
            amount=_f(m.get("amount")),
            turnover_rate=_f(m.get("turnover_rate")),
            volume_ratio=_f(m.get("volume_ratio")),
            pe=_f(m.get("pe")),
            pb=_f(m.get("pb")),
            amplitude=_f(m.get("amplitude")),
            float_mv=_f(m.get("float_mv")),
            total_mv=_f(m.get("total_mv")),
            time=m.get("time") or "",
            limit_up=_f(m.get("limit_up")),
            limit_down=_f(m.get("limit_down")),
            main_inflow=_f(m.get("main_inflow")),
        )
        for i in range(1, 6):
            fields[f"bid{i}_price"] = _f(m.get(f"bid{i}_price"))
            fields[f"bid{i}_vol"] = _f(m.get(f"bid{i}_vol"))
            fields[f"ask{i}_price"] = _f(m.get(f"ask{i}_price"))
            fields[f"ask{i}_vol"] = _f(m.get(f"ask{i}_vol"))
        return cls(**fields)


@dataclass
class KLine:
    """一根K线（日线/周线/月线）。volume 单位手，amount 单位元。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0
    amount: float = 0.0
    change_pct: Optional[float] = None
    turnover: Optional[float] = None


@dataclass
class NewsItem:
    title: str
    date: str = ""
    url: str = ""
    summary: str = ""
    source: str = ""


@dataclass
class Suggestion:
    """搜索联想结果。"""

    code: str
    name: str
    market_num: str = ""  # 东财市场号: 1=沪 0=深


@dataclass
class TradeSignal:
    """策略监听产生的交易机会信号（buy/sell）。"""

    time: str = ""
    code: str = ""
    name: str = ""
    strategy: str = ""
    side: str = "buy"                    # buy / sell
    price: Optional[float] = None        # 现价 = 参考买入成本
    stop_price: Optional[float] = None   # 建议止损价
    target_price: Optional[float] = None  # 建议目标价
    risk_score: float = 0.0              # 0~100，越低越稳
    hit_rules: List[str] = field(default_factory=list)
    risk_notes: List[str] = field(default_factory=list)
    reason: str = ""
    # v1.3 机会表达
    sparkline: List[float] = field(default_factory=list)  # 近30日收盘价
    op_score: float = 0.0                # 机会分 0~100
    grade: str = ""                      # A / B / C

    def key(self) -> str:
        """去重键：代码|策略|方向|日期。"""
        return f"{self.code}|{self.strategy}|{self.side}|{self.time[:10]}"


@dataclass
class Trade:
    """回测逐笔交易。"""

    code: str = ""
    name: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    reason: str = ""


@dataclass
class MinutePoint:
    """分时数据点。time 形如 '09:30'；volume 为该分钟量(手)。"""

    time: str = ""
    price: Optional[float] = None
    avg: Optional[float] = None
    volume: float = 0.0


@dataclass
class MinuteSeries:
    date: str = ""
    prev_close: Optional[float] = None
    points: List["MinutePoint"] = field(default_factory=list)


@dataclass
class Board:
    """行业板块快照。amount_yi 单位亿元。"""

    name: str = ""
    change_pct: Optional[float] = None
    amount_yi: Optional[float] = None
    count: int = 0
    leader_code: str = ""
    leader_name: str = ""
    leader_pct: Optional[float] = None
