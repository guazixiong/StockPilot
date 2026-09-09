"""技术指标引擎（纯 Python，无 pandas）。

输入 OHLCV 数组，输出末值快照与形态判定，供策略引擎 / 选股 / AI 上下文共用。
所有函数对短序列安全（返回 None / 空判定，不抛异常）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .models import KLine

# ---------------------------------------------------------------- 基础序列


def sma(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 >= n:
            out[i] = sum(values[i + 1 - n:i + 1]) / n
    return out


def ema(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def macd(close: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = ema(dif, signal)
    hist = [2.0 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, hist


def rsi(close: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """Wilder 平滑 RSI。"""
    out: List[Optional[float]] = [None] * len(close)
    if len(close) <= n:
        return out
    gains = [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses = [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    def _val(g: float, l: float) -> float:
        return 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)

    out[n] = _val(avg_gain, avg_loss)
    for i in range(n + 1, len(close)):
        avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        out[i] = _val(avg_gain, avg_loss)
    return out


def kdj(high: Sequence[float], low: Sequence[float],
        close: Sequence[float], n: int = 9, k_s: int = 3, d_s: int = 3):
    k_out: List[Optional[float]] = [None] * len(close)
    d_out: List[Optional[float]] = [None] * len(close)
    j_out: List[Optional[float]] = [None] * len(close)
    k = d = 50.0
    for i in range(len(close)):
        lo = min(low[max(0, i - n + 1):i + 1])
        hi = max(high[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (close[i] - lo) / (hi - lo) * 100.0
        k = (k * (k_s - 1) + rsv) / k_s
        d = (d * (d_s - 1) + k) / d_s
        k_out[i], d_out[i], j_out[i] = k, d, 3 * k - 2 * d
    return k_out, d_out, j_out


def atr_pct(klines, n: int = 14) -> Optional[float]:
    """平均真实波幅占价格的百分比（v6.0 盯盘用：自适应异动阈值）。

    ATR% = mean(TR, n) / 最新收盘 × 100——高波动股该值大，异动阈值随之抬升。
    """
    if len(klines) < n + 1:
        return None
    trs = []
    prev_close = klines[0].close
    for k in klines[-(n + 1):-0] if False else klines[1:]:
        tr = max(k.high - k.low,
                 abs(k.high - prev_close),
                 abs(k.low - prev_close))
        trs.append(tr)
        prev_close = k.close
    trs = trs[-n:]
    last_close = klines[-1].close
    if not trs or last_close <= 0:
        return None
    return sum(trs) / len(trs) / last_close * 100


def boll(close: Sequence[float], n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    up: List[Optional[float]] = [None] * len(close)
    low: List[Optional[float]] = [None] * len(close)
    for i in range(n - 1, len(close)):
        window = close[i + 1 - n:i + 1]
        m = mid[i]
        if m is None:
            continue
        var = sum((x - m) ** 2 for x in window) / n
        sd = var ** 0.5
        up[i], low[i] = m + k * sd, m - k * sd
    return mid, up, low


def bias(close: Sequence[float], n: int) -> List[Optional[float]]:
    """乖离率 BIAS：(收盘-MA)/MA*100。"""
    ma = sma(close, n)
    return [None if (m is None or m == 0) else (c - m) / m * 100
            for c, m in zip(close, ma)]


def wr(high: Sequence[float], low: Sequence[float],
       close: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """威廉指标 WR：100*(Hn-C)/(Hn-Ln)。"""
    out: List[Optional[float]] = [None] * len(close)
    for i in range(n - 1, len(close)):
        hi = max(high[i + 1 - n:i + 1])
        lo = min(low[i + 1 - n:i + 1])
        out[i] = 50.0 if hi == lo else (hi - close[i]) / (hi - lo) * 100
    return out


def cci(high: Sequence[float], low: Sequence[float],
        close: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """CCI 顺势指标。"""
    out: List[Optional[float]] = [None] * len(close)
    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    for i in range(n - 1, len(close)):
        window = tp[i + 1 - n:i + 1]
        m = sum(window) / n
        md = sum(abs(x - m) for x in window) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return out


def roc(close: Sequence[float], n: int = 12) -> List[Optional[float]]:
    """变动率 ROC：*(100/C-n)。"""
    out: List[Optional[float]] = [None] * len(close)
    for i in range(n, len(close)):
        base = close[i - n]
        out[i] = None if base == 0 else (close[i] - base) / base * 100
    return out


def psy(close: Sequence[float], n: int = 12) -> List[Optional[float]]:
    """心理线 PSY：N日内上涨天数占比。"""
    out: List[Optional[float]] = [None] * len(close)
    for i in range(n, len(close)):
        wins = sum(1 for j in range(i - n + 1, i + 1)
                   if close[j] > close[j - 1])
        out[i] = wins / n * 100
    return out


def obv_rising(close: Sequence[float], volume: Sequence[float]) -> bool:
    """OBV 能量潮方向：近 10 日 OBV 高于 10 日前（简化判定）。"""
    if len(close) < 11:
        return False
    obv = 0.0
    obvs = [0.0]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv += volume[i] if i < len(volume) else 0
        elif close[i] < close[i - 1]:
            obv -= volume[i] if i < len(volume) else 0
        obvs.append(obv)
    return obvs[-1] > obvs[-10]


def dmi(high: Sequence[float], low: Sequence[float],
        close: Sequence[float], n: int = 14):
    """DMI：返回 (plus_di, minus_di, adx 序列)。Wilder 平滑。"""
    length = len(close)
    plus_di = [None] * length
    minus_di = [None] * length
    adx = [None] * length
    if length < n * 2:
        return plus_di, minus_di, adx
    trs, pdms, mdms = [], [], []
    for i in range(1, length):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                 abs(low[i] - close[i - 1]))
        pdm = high[i] - high[i - 1]
        mdm = low[i - 1] - low[i]
        pdm = pdm if (pdm > mdm and pdm > 0) else 0.0
        mdm = mdm if (mdm > pdm and mdm > 0) else 0.0
        trs.append(tr)
        pdms.append(pdm)
        mdms.append(mdm)
    def _wilder(vals, n):
        out = []
        if len(vals) < n:
            return out
        cur = sum(vals[:n])
        out.append(cur)
        for v in vals[n:]:
            cur = cur - cur / n + v
            out.append(cur)
        return out

    tr_s = _wilder(trs, n)
    pdm_s = _wilder(pdms, n)
    mdm_s = _wilder(mdms, n)
    dxs = []
    start = n  # 序列尾对齐第 n..length-1 根
    for k in range(len(tr_s)):
        i = start + k
        tr_v, pdm_v, mdm_v = tr_s[k], pdm_s[k], mdm_s[k]
        if not tr_v:
            plus_di[i] = minus_di[i] = 0.0
        else:
            plus_di[i] = pdm_v / tr_v * 100
            minus_di[i] = mdm_v / tr_v * 100
        pdi, mdi = plus_di[i] or 0, minus_di[i] or 0
        dxs.append(0.0 if (pdi + mdi) == 0 else abs(pdi - mdi) / (pdi + mdi) * 100)
    # ADX = DX 的 Wilder 平滑
    m = len(dxs)
    if m >= n:
        cur = sum(dxs[:n])
        for k in range(m):
            if k >= n:
                cur = cur - cur / n + dxs[k]
            adx[start + k] = cur
    return plus_di, minus_di, adx


def pct_change(close: Sequence[float], n: int) -> List[Optional[float]]:
    """N 日涨幅%。"""
    out: List[Optional[float]] = [None] * len(close)
    for i in range(n, len(close)):
        base = close[i - n]
        out[i] = None if base == 0 else (close[i] - base) / base * 100
    return out


def streak(close: Sequence[float]) -> tuple:
    """连涨/连跌天数（截至末根，含末根）。"""
    up = down = 0
    if len(close) < 2:
        return 0, 0
    last = close[-1]
    i = len(close) - 2
    while i >= 0 and close[i] != last:
        if last > close[i]:
            up += 1
            last = close[i]
        else:
            down += 1
            last = close[i]
        i -= 1
    return up, down


# ---------------------------------------------------------------- 综合分析

_BOOL_KEYS = ("macd_golden", "macd_dead", "macd_bull", "kdj_golden",
              "ma_bull", "ma_bear", "ma20_rising")


def _cross(a: Sequence[Optional[float]], b: Sequence[Optional[float]]) -> tuple:
    """返回 (是否金叉, 是否死叉) 基于 最后两根。"""
    if len(a) < 2:
        return False, False
    a1, a0, b1, b0 = a[-2], a[-1], b[-2], b[-1]
    if None in (a1, a0, b1, b0):
        return False, False
    golden = a1 <= b1 and a0 > b0
    dead = a1 >= b1 and a0 < b0
    return golden, dead


def code_prefix(klines: List[KLine], n: int) -> str:
    """涨停阈值分流：analyze 无 code 入参，保守默认主板（9.5%）；
    创科板由 build_ctx（有 quote.code）二次纠正。"""
    return "00"


def analyze(klines: List[KLine]) -> Dict[str, object]:
    """计算末根K线的全量指标快照。数值缺失为 None，布尔形态缺失为 False。"""
    ctx: Dict[str, object] = {}
    if not klines:
        return ctx
    closes = [k.close for k in klines]
    highs = [k.high for k in klines]
    lows = [k.low for k in klines]
    vols = [k.volume for k in klines]
    n = len(closes)

    for w, key in ((5, "ma5"), (10, "ma10"), (20, "ma20"), (30, "ma30"),
                   (60, "ma60"), (120, "ma120"), (250, "ma250")):
        ctx[key] = sma(closes, w)[-1]
    ctx["ema12"], ctx["ema26"] = ema(closes, 12)[-1], ema(closes, 26)[-1]
    dif, dea, hist = macd(closes)
    ctx["dif"], ctx["dea"], ctx["macd_hist"] = dif[-1], dea[-1], hist[-1]
    for w, key in ((6, "rsi6"), (12, "rsi12"), (24, "rsi24")):
        ctx[key] = rsi(closes, w)[-1]
    k_v, d_v, j_v = kdj(highs, lows, closes)
    ctx["k"], ctx["d"], ctx["j"] = k_v[-1], d_v[-1], j_v[-1]
    mid, up, low = boll(closes)
    ctx["boll_mid"], ctx["boll_up"], ctx["boll_low"] = mid[-1], up[-1], low[-1]
    # v6.0 盯盘：ATR% 与昨日在 MA20 上方标记（跌破 MA20 告警需昨日未破）
    from .models import KLine as _K  # noqa: F401  类型提示
    ctx["atr_pct"] = atr_pct(klines)
    _prev_ma = ctx.get("ma20")
    ctx["prev_above_ma20"] = bool(
        len(klines) >= 2 and isinstance(_prev_ma, (int, float))
        and klines[-2].close >= _prev_ma)

    # v1.8 主流指标扩充
    for w, key in ((6, "bias6"), (12, "bias12"), (24, "bias24")):
        ctx[key] = bias(closes, w)[-1]
    ctx["wr14"] = wr(highs, lows, closes, 14)[-1]
    ctx["cci14"] = cci(highs, lows, closes, 14)[-1]
    ctx["roc12"] = roc(closes, 12)[-1]
    ctx["psy12"] = psy(closes, 12)[-1]
    ctx["obv_rising"] = obv_rising(closes, vols)
    plus_di, minus_di, adx = dmi(highs, lows, closes)
    ctx["plus_di"], ctx["minus_di"], ctx["adx"] = (
        plus_di[-1], minus_di[-1], adx[-1])
    p1, p2 = (plus_di[-1] or 0), (minus_di[-1] or 0)
    pp1, pp2 = (plus_di[-2] or 0), (minus_di[-2] or 0)
    ctx["dmi_golden"] = bool(pp1 <= pp2 and p1 > p2)
    for w in (5, 10, 20, 60):
        ctx[f"chg{w}d"] = pct_change(closes, w)[-1]
    up_days, down_days = streak(closes)
    ctx["up_days"], ctx["down_days"] = up_days, down_days
    h20 = ctx.get("high20_prev")
    if isinstance(h20, float) and h20 > 0:
        ctx["dd_from_high20"] = (closes[-1] / max(max(highs[-21:]), h20) - 1) * 100
    else:
        ctx["dd_from_high20"] = None
    # Sequoia 形态策略上下文（v3.0）
    if n >= 2:
        k0, k1 = klines[-1], klines[-2]
        ctx["is_yang"] = k0.close > k0.open
        ctx["is_bear_today"] = k0.close < k0.open
        # 涨停判定（主板9.5%阈值，创科按19.5%由代码首位分流）
        limit_th = 0.195 if code_prefix(klines, n) in ("30", "68") else 0.095
        try:
            prev2 = klines[-3].close if n >= 3 else k1.close
            ctx["prev_limit_up"] = k1.close >= prev2 * (1 + limit_th)
            ctx["prev_limit_down"] = k1.close <= prev2 * (1 - limit_th)
        except Exception:  # noqa: BLE001
            ctx["prev_limit_up"] = ctx["prev_limit_down"] = False
        ctx["vol_vs_prev"] = (k0.volume / k1.volume) if k1.volume else None
        ctx["_prev2_close"] = klines[-3].close if n >= 3 else k1.close
        prev_ma5 = sma(closes[:-1], 5)
        prev_ma20 = sma(closes[:-1], 20)
        ctx["prev_ma5"] = prev_ma5[-1] if prev_ma5 else None
        ctx["prev_ma20"] = prev_ma20[-1] if prev_ma20 else None
        prev_ma60 = sma(closes[:-1], 60)
        pma20, pma60 = ctx.get("ma20"), prev_ma60[-1] if prev_ma60 else None
        # 注意：ma20 为"含当根"的20日均线；Sequoia 用"昨日的 ma20>ma60"
        ctx["prev_ma20_gt_ma60"] = bool(
            isinstance(ctx["prev_ma20"], float) and isinstance(pma60, float)
            and ctx["prev_ma20"] > pma60)
    else:
        ctx["is_yang"] = ctx["is_bear_today"] = False
        ctx["prev_limit_up"] = ctx["prev_limit_down"] = False
        ctx["vol_vs_prev"] = None
        ctx["prev_ma5"] = ctx["prev_ma20"] = None
        ctx["prev_ma20_gt_ma60"] = False
    # 量 vs 20日均量（不含当日）
    if n >= 22 and sum(vols[-21:-1]) > 0:
        ctx["vol_vs_ma20"] = vols[-1] / (sum(vols[-21:-1]) / 20)
    else:
        ctx["vol_vs_ma20"] = None
    # 40/10日振幅比 + 高位抗跌（高窄旗形）
    if n >= 40:
        h40 = max(highs[-40:]); l40 = min(lows[-40:])
        h10 = max(highs[-10:]); l10 = min(lows[-10:])
        ctx["amp40"] = (h40 / l40) if l40 > 0 else None
        ctx["amp10"] = (h10 / l10) if l10 > 0 else None
        ctx["high10_hold"] = bool(l10 >= h40 * 0.8) if h40 > 0 else False
    else:
        ctx["amp40"] = ctx["amp10"] = None
        ctx["high10_hold"] = False

    # 筹码分布（v2.3）
    from . import cyq as _cyq
    cyq = _cyq.cyq_distribution(klines)
    ctx["cyq_profit"] = cyq["profit_ratio"]      # 获利盘%
    ctx["cyq_avg_cost"] = cyq["avg_cost"]         # 平均成本
    ctx["cyq_near"] = cyq["near_current"]         # 现价附近筹码占比%
    ctx["cyq_conc"] = cyq["concentration"]         # 成本集中度%（越小越集中）
    if n >= 6 and sum([k.amount for k in klines[-5:]]) > 0:
        amts = [k.amount for k in klines]
        ma5_amt = sum(amts[-5:]) / 5
        ctx["amt_ratio_5d"] = (amts[-1] / ma5_amt) if ma5_amt > 0 else None
    else:
        ctx["amt_ratio_5d"] = None

    # 近 N 日最高/最低（不含当日，供"创N日新高"比较）
    for w in (10, 20, 30, 60):
        seg_h = highs[max(0, n - w):n - 1] if n > 1 else []
        seg_l = lows[max(0, n - w):n - 1] if n > 1 else []
        ctx[f"high{w}_prev"] = max(seg_h) if seg_h else None
        ctx[f"low{w}_prev"] = min(seg_l) if seg_l else None

    # 量能：今日量 / 5日均量
    if n >= 6 and sum(vols[-6:-1]) > 0:
        ctx["vol_vs_ma5"] = vols[-1] / (sum(vols[-6:-1]) / 5)
    else:
        ctx["vol_vs_ma5"] = None

    ma5, ma10, ma20, ma60 = (ctx.get(f"ma{w}") for w in (5, 10, 20, 60))
    ctx["ma_bull"] = all(v is not None for v in (ma5, ma10, ma20, ma60)) and \
        ma5 > ma10 > ma20 > ma60  # type: ignore[operator]
    ctx["ma_bear"] = all(v is not None for v in (ma5, ma10, ma20, ma60)) and \
        ma5 < ma10 < ma20 < ma60  # type: ignore[operator]
    if n >= 4 and ctx.get("ma20") is not None and sma(closes, 20)[-4] is not None:
        ctx["ma20_rising"] = bool(closes and ma20 and ma20 > sma(closes, 20)[-4])
    else:
        ctx["ma20_rising"] = False

    golden, dead = _cross(dif, dea)
    ctx["macd_golden"], ctx["macd_dead"] = golden, dead
    ctx["macd_bull"] = bool(
        dif[-1] is not None and dea[-1] is not None and dif[-1] > dea[-1])
    kg, kd_ = _cross(k_v, d_v)
    ctx["kdj_golden"] = kg

    ctx["prev_close"] = closes[-2] if n >= 2 else None
    ctx["prev_low"] = lows[-2] if n >= 2 else None
    ctx["high20_prev"] = ctx.get("high20_prev")
    return ctx


def kline_summary(klines: List[KLine], k: int = 10) -> str:
    """最近 k 根K线的文字摘要（AI 上下文用）。"""
    if not klines:
        return "（无K线数据）"
    tail = klines[-k:]
    rows = [f"{x.date} 开{x.open:.2f} 收{x.close:.2f} 高{x.high:.2f} "
            f"低{x.low:.2f} 涨跌{x.change_pct if x.change_pct is not None else '-'}%"
            for x in tail]
    return "\n".join(rows)


def signals_text(ctx: Dict[str, object]) -> List[str]:
    """形态判定文字（AI/详情页用）。"""
    out: List[str] = []
    if ctx.get("ma_bull"):
        out.append("均线多头排列(MA5>MA10>MA20>MA60)")
    if ctx.get("ma_bear"):
        out.append("均线空头排列")
    if ctx.get("macd_golden"):
        out.append("MACD金叉")
    if ctx.get("macd_dead"):
        out.append("MACD死叉")
    r6 = ctx.get("rsi6")
    if isinstance(r6, float):
        if r6 > 70:
            out.append(f"RSI6={r6:.0f} 超买")
        elif r6 < 30:
            out.append(f"RSI6={r6:.0f} 超卖")
    j = ctx.get("j")
    if isinstance(j, float):
        if j > 100:
            out.append("KDJ-J超买")
        elif j < 0:
            out.append("KDJ-J超卖")
    return out
