"""分时走势分析器（v6.1）：盯盘的"走势"层——用户指出 v6.0 只是日K条件检测。

盯盘应该盯**分时**：盘中价格与均价（VWAP）的相对关系、量能节奏、
日内形态（急拉/跳水/冲高回落/震荡企稳/尾盘异动）——这才是"根据
分时走的盯盘"，而不是拿日K指标做静态条件判断。

纯函数：输入 MinuteSeries（分时）+ 持仓上下文 → 走势告警。
与日K风控规则（watch.check_position）互补：日K管"位置"（止损止盈/均线），
分时管"当下的盘口行为"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class IntradaySignal:
    """一个分时走势信号。"""
    kind: str            # 形态键（见 KIND_TEXT）
    title: str           # 一句话
    detail: str          # 数据依据
    level: str           # danger / warning / chance / info
    action: str          # 建议动作
    window: str = ""     # 发生时段（如 10:05-10:15）


KIND_TEXT = {
    "surge_spike": "分时急拉",       # 短窗急涨（量价齐升更可信）
    "plunge_spike": "分时急跌",
    "fade_from_high": "冲高回落",     # 早盘冲高后跌回（日内诱多特征）
    "recover_from_low": "触底回升",   # 反向：跌深后收复
    "vwap_deviate": "大幅偏离均价",   # 乖离预警（追高风险/超跌机会）
    "vwap_reclaim": "收复均价线",     # 站上VWAP——日内转强
    "vwap_lose": "跌落均价线",        # 跌破VWAP——日内转弱
    "late_move": "尾盘异动",         # 14:30 后放量方向选择
    "volume_burst": "量能脉冲",      # 单位时间量能远超均值
    "listless": "缩量横盘",          # 无量磨叽（机会成本提示）
}


def analyze_intraday(series, tolerance: float = 0.6) -> List[IntradaySignal]:
    """对一只持仓的分时跑走势形态识别。

    series: MinuteSeries（points 含 time/price/avg/volume）
    tolerance: "急动"最小幅度%（默认 0.6%——分时级别的"异动"门槛，
               远小于日线 3%，因为分时噪声本来就大）
    """
    out: List[IntradaySignal] = []
    pts = [p for p in (series.points or []) if p.price]
    if len(pts) < 10 or not series.prev_close:
        return out
    prev_close = float(series.prev_close)
    prices = [p.price for p in pts]
    vols = [p.volume for p in pts]
    n = len(pts)

    def pct(a, b):
        return (a - b) / b * 100 if b else 0.0

    # ---- 1) 短窗急动（10 分钟窗口滚动扫描）----
    win = min(10, n - 1)
    best_up = best_down = None      # (幅度, 起点, 终点)
    for i in range(0, n - win):
        seg = prices[i:i + win + 1]
        d = pct(seg[-1], seg[0])
        if best_up is None or d > best_up[0]:
            best_up = (d, pts[i].time, pts[i + win].time, i, i + win)
        if best_down is None or d < best_down[0]:
            best_down = (d, pts[i].time, pts[i + win].time, i, i + win)
    if best_up and best_up[0] >= tolerance:
        i0, i1 = best_up[3], best_up[4]
        seg_vol = sum(vols[i0:i1 + 1])
        avg_vol = sum(vols) / n
        with_vol = win * avg_vol > 0 and seg_vol >= win * avg_vol * 1.2
        out.append(IntradaySignal(
            kind="surge_spike", level="chance" if with_vol else "info",
            title=f"10分钟急拉 {best_up[0]:+.1f}%" + ("（放量）" if with_vol else "（缩量）"),
            detail=f"{best_up[1]}~{best_up[2]} {prices[i0]:.2f}→{prices[i1]:.2f}",
            action="放量急拉可看高一线；缩量急拉注意冲高回落",
            window=f"{best_up[1]}-{best_up[2]}"))
    if best_down and best_down[0] <= -tolerance:
        i0, i1 = best_down[3], best_down[4]
        out.append(IntradaySignal(
            kind="plunge_spike", level="danger",
            title=f"10分钟急跌 {best_down[0]:+.1f}%",
            detail=f"{best_down[1]}~{best_down[2]} {prices[i0]:.2f}→{prices[i1]:.2f}",
            action="急跌勿慌，看均价线是否失守；跌破考虑减仓",
            window=f"{best_down[1]}-{best_down[2]}"))

    # ---- 2) 冲高回落 / 触底回升（日内最高最低 vs 现价）----
    hi_i = prices.index(max(prices))
    lo_i = prices.index(min(prices))
    last = prices[-1]
    last_pt = pts[-1]
    day_hi, day_lo = prices[hi_i], prices[lo_i]
    if hi_i < n * 0.6 and pct(last, day_hi) <= -tolerance and \
            pct(day_hi, prev_close) >= tolerance:
        out.append(IntradaySignal(
            kind="fade_from_high", level="warning",
            title=f"冲高回落：最高 {day_hi:.2f} 现回落至 {last:.2f}",
            detail=f"高点出现在 {pts[hi_i].time}（{pct(day_hi, prev_close):+.1f}%），"
                   f"现距高点 {pct(last, day_hi):+.1f}%",
            action="日内诱多形态，谨慎追涨；持仓者可部分止盈",
            window=f"高点 {pts[hi_i].time}"))
    if lo_i < n * 0.6 and pct(last, day_lo) >= tolerance and \
            pct(day_lo, prev_close) <= -tolerance:
        out.append(IntradaySignal(
            kind="recover_from_low", level="chance",
            title=f"触底回升：最低 {day_lo:.2f} 现收复至 {last:.2f}",
            detail=f"低点 {pts[lo_i].time}（{pct(day_lo, prev_close):+.1f}%），"
                   f"现反弹 {pct(last, day_lo):+.1f}%",
            action="日内V型修复，观察均价线收复确认",
            window=f"低点 {pts[lo_i].time}"))

    # ---- 3) 均价线（VWAP）关系：乖离/收复/跌落 ----
    vwap = last_pt.avg
    if vwap:
        dev = pct(last, vwap)
        if dev >= 1.5:
            out.append(IntradaySignal(
                kind="vwap_deviate", level="warning",
                title=f"现价高于均价 {dev:+.1f}%（乖离过大）",
                detail=f"现价 {last:.2f}，分时均价 {vwap:.2f}",
                action="短线乖离大，追高风险；持仓者可部分止盈"))
        elif dev <= -1.5:
            out.append(IntradaySignal(
                kind="vwap_deviate", level="chance",
                title=f"现价低于均价 {dev:+.1f}%（超跌乖离）",
                detail=f"现价 {last:.2f}，分时均价 {vwap:.2f}",
                action="日内超卖，激进者轻仓搏回归均价"))
        # 趋势转换：最近 10 分钟跨过均价线
        if n >= 10:
            below = [p for p in pts[-10:] if p.avg]
            if len(below) >= 5:
                was_below = below[0].price < below[0].avg
                is_above = last > vwap
                if was_below and is_above:
                    out.append(IntradaySignal(
                        kind="vwap_reclaim", level="chance",
                        title=f"站上分时均价线（{vwap:.2f}）",
                        detail=f"10分钟内从下方收复，现价 {last:.2f}",
                        action="日内转强信号，持有观察"))
                elif not was_below and last < vwap:
                    out.append(IntradaySignal(
                        kind="vwap_lose", level="warning",
                        title=f"跌落分时均价线（{vwap:.2f}）",
                        detail=f"10分钟内从上方跌破，现价 {last:.2f}",
                        action="日内转弱信号，注意止损纪律"))

    # ---- 4) 尾盘异动（14:30 后的方向与量能）----
    tail = [(i, p) for i, p in enumerate(pts)
            if p.time >= "14:30"]
    if len(tail) >= 5:
        t_prices = [p.price for _, p in tail]
        # 尾盘方向 = 尾盘段内变化 或 相对尾盘前 30 分钟基准（捕捉 14:30 跳变）
        t_change = pct(t_prices[-1], t_prices[0])
        pre_idx = tail[0][0]
        base = prices[max(0, pre_idx - 30)] if pre_idx > 0 else t_prices[0]
        vs_pre = pct(t_prices[-1], base)
        eff = t_change if abs(t_change) >= abs(vs_pre) else vs_pre
        pre_avg_vol = (sum(vols) / n) if n else 0
        tail_avg_vol = sum(p.volume for _, p in tail) / len(tail)
        if abs(eff) >= tolerance * 0.7 or \
                (pre_avg_vol > 0 and tail_avg_vol >= pre_avg_vol * 2):
            direction = "尾盘拉升" if eff > 0 else "尾盘跳水"
            vol_note = (f"，量能 {tail_avg_vol / pre_avg_vol:.1f}×日均"
                        if pre_avg_vol > 0 else "")
            out.append(IntradaySignal(
                kind="late_move",
                level="warning" if eff < 0 else "info",
                title=f"{direction} {eff:+.1f}%" + vol_note,
                detail=f"14:30 后 {t_prices[0]:.2f}→{t_prices[-1]:.2f}"
                       f"（相对尾盘前基准 {base:.2f}）",
                action="尾盘定方向：拉尾盘次日惯性高开概率大；跳水注意次日低开风险"))

    # ---- 5) 量能脉冲 / 缩量横盘 ----
    if n >= 20:
        avg_vol = sum(vols) / n
        max_v = max(vols)
        max_vi = vols.index(max_v)
        if avg_vol > 0 and max_v >= avg_vol * 6:
            out.append(IntradaySignal(
                kind="volume_burst", level="info",
                title=f"量能脉冲（{pts[max_vi].time} 单分钟 {max_v:.0f} 手",
                detail=f"为盘中均量的 {max_v / avg_vol:.1f} 倍——该时刻或有消息/主力动作",
                action="结合脉冲后的价格方向判断多空",
                window=pts[max_vi].time))
        # 缩量横盘：整体振幅 < 0.5% 且量能 < 前期一半
        amp = (day_hi - day_lo) / prev_close * 100
        first_half_vol = sum(vols[:n // 2]) / max(n // 2, 1)
        second_half_vol = sum(vols[n // 2:]) / max(n - n // 2, 1)
        if amp < 0.5 and first_half_vol > 0 and \
                second_half_vol < first_half_vol * 0.5 and n >= 60:
            out.append(IntradaySignal(
                kind="listless", level="info",
                title=f"缩量横盘（振幅仅 {amp:.1f}%）",
                detail="后半场量能明显萎缩，多空都在等方向",
                action="横盘末端变盘窗口，盯住均价线突破方向"))

    return out


def intraday_summary(signals: List[IntradaySignal]) -> str:
    """分时信号 → 一句话分时盘口（盯盘状态栏用）。"""
    if not signals:
        return "分时盘口平稳，无明显形态"
    parts = [s.title for s in signals[:4]]
    return "分时：" + "；".join(parts)
