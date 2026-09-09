"""v6.1：分时走势盯盘（用户指出 v6.0 是"条件检测"非分时驱动）单测。"""
from stockpilot.core import intraday
from stockpilot.core.models import MinutePoint, MinuteSeries


def _mk(points, prev_close=10.0):
    return MinuteSeries(date="20260907", prev_close=prev_close,
                        points=[MinutePoint(time=t, price=p, avg=a, volume=v)
                                for t, p, a, v in points])


def _ramp(n=120, up_end=60, up_to=10.8, back_to=10.2, prev=10.0):
    """前段爬升到 up_to、后段回落到 back_to 的分时。"""
    pts = []
    for i in range(n):
        t = f"{9 + i // 60:02d}:{(30 + i) % 60:02d}"
        p = prev + (up_to - prev) * i / up_end if i < up_end             else up_to - (up_to - back_to) * (i - up_end) / (n - up_end)
        avg = prev + 0.3 * i / n
        pts.append((t, round(p, 2), round(avg, 2), 500.0))
    return pts


def test_empty_and_short_series():
    """空/过短分时不产信号（防御）。"""
    assert intraday.analyze_intraday(MinuteSeries()) == []
    short = _mk([(f"09:{30 + i:02d}", 10.0, 10.0, 100.0) for i in range(5)])
    assert intraday.analyze_intraday(short) == []


def test_fade_from_high():
    """冲高回落：高点在前 60% 时段+回落超阈值+曾显著上涨。"""
    sigs = intraday.analyze_intraday(_mk(_ramp()))
    kinds = [s.kind for s in sigs]
    assert "fade_from_high" in kinds
    a = next(s for s in sigs if s.kind == "fade_from_high")
    assert a.level == "warning" and "诱多" in a.action


def test_surge_and_plunge_spike():
    """10 分钟窗急拉/急跌识别。"""
    pts = []
    for i in range(120):
        t = f"{9 + i // 60:02d}:{(30 + i) % 60:02d}"
        p = 10.0 if i < 50 else 10.0 + 0.12 * (i - 50)   # 急拉段
        pts.append((t, round(p, 2), 10.05, 300.0))
    sigs = intraday.analyze_intraday(_mk(pts))
    assert any(s.kind == "surge_spike" and s.level in ("chance", "info")
               for s in sigs)
    pts2 = []
    for i in range(120):
        t = f"{9 + i // 60:02d}:{(30 + i) % 60:02d}"
        p = 10.0 if i < 50 else 10.0 - 0.12 * (i - 50)  # 急跌段
        pts2.append((t, round(p, 2), 9.95, 300.0))
    sigs2 = intraday.analyze_intraday(_mk(pts2))
    a = next(s for s in sigs2 if s.kind == "plunge_spike")
    assert a.level == "danger"


def test_vwap_deviate_and_reclaim():
    """乖离预警 + 均价线收复。"""
    # 高乖离：现价远高于均价
    hi = [(f"{9 + i // 60:02d}:{(30 + i) % 60:02d}", 10.5, 10.0, 400.0)
          for i in range(120)]
    sigs = intraday.analyze_intraday(_mk(hi))
    a = next(s for s in sigs if s.kind == "vwap_deviate")
    assert a.level == "warning" and "追高" in a.action
    # 收复均价：前 111 点在均价下、后 9 点站上（近10分钟窗首点仍在下方）
    pts = []
    for i in range(120):
        t = f"{9 + i // 60:02d}:{(30 + i) % 60:02d}"
        p = 9.9 if i < 111 else 10.15   # 现价 10.15 对均价 10.0 乖离 1.5% 内
        pts.append((t, p, 10.0, 300.0))
    sigs2 = intraday.analyze_intraday(_mk(pts))
    assert any(s.kind == "vwap_reclaim" for s in sigs2)


def test_late_move():
    """尾盘异动：14:30 阶跃拉尾盘（尾盘段内平但相对尾盘前基准 +8%）。"""
    pts = []
    for h in range(9, 16):
        ms = range(30, 60) if h == 9 else range(0, 60)
        for m in ms:
            t = f"{h:02d}:{m:02d}"
            if t < "09:30" or t >= "15:00":
                continue
            p = 10.8 if t >= "14:30" else 10.0
            pts.append((t, p, 10.05, 400.0))
    sigs = intraday.analyze_intraday(_mk(pts))
    kinds = [s.kind for s in sigs]
    assert "late_move" in kinds
    a = next(s for s in sigs if s.kind == "late_move")
    assert "尾盘拉升" in a.title


def test_volume_burst():
    """量能脉冲：单分钟量能 ≥ 均量 6 倍。"""
    pts = []
    for i in range(120):
        t = f"{9 + i // 60:02d}:{(30 + i) % 60:02d}"
        v = 6000.0 if i == 40 else 300.0
        pts.append((t, 10.0 + 0.001 * i, 10.05, v))
    sigs = intraday.analyze_intraday(_mk(pts))
    assert any(s.kind == "volume_burst" for s in sigs)


def test_summary():
    """分时摘要拼装。"""
    assert "平稳" in intraday.intraday_summary([])
