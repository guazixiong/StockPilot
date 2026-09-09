"""程序入口。

- 无参数：启动 PySide6 桌面客户端
- --smoke：离线自检 + 真实接口连通性验证（发布前验证用）
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .core.storage import data_dir


def setup_logging() -> Path:
    logs = data_dir() / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            logs / "app.log", maxBytes=1024 * 1024, backupCount=3,
            encoding="utf-8")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[handler, logging.StreamHandler()])
    except OSError:
        logging.basicConfig(level=logging.INFO)
    return logs


def smoke() -> int:
    """自检：离线确定性用例 + 真实接口连通性（后者失败仅告警）。"""
    from .core import selftest
    from .core.providers.base import HttpClient, normalize_code, to_market_symbol, to_secid
    from .core.providers.eastmoney import EastmoneyProvider
    from .core.providers.tencent import TencentProvider

    print(f"{APP_NAME} v{__version__} 自检")
    print("=" * 60)
    ok = True

    print("\n[1] 离线自检（指标/策略/回测/通知）")
    for name, passed, detail in selftest.run_offline_selftest():
        mark = "PASS" if passed else "FAIL"
        ok = ok and passed
        print(f"  [{mark}] {name}: {detail}")

    print("\n[2] 代码映射")
    cases = {"600519": ("sh600519", "1.600519"), "000001": ("sz000001", "0.000001"),
             "300750": ("sz300750", "0.300750"), "830799": ("bj830799", "0.830799"),
             "688981": ("sh688981", "1.688981")}
    for code, (sym, secid) in cases.items():
        got = (to_market_symbol(code), to_secid(code))
        status = "PASS" if got == (sym, secid) else "FAIL"
        ok = ok and got == (sym, secid)
        print(f"  [{status}] {code} -> {got}")

    print("\n[3] 真实接口连通性（与应用相同的多级降级链；失败为 WARN）")
    from .core.storage import Config
    from .ui.context import AppContext
    ctx = AppContext(Config())
    try:
        q = ctx.get_quotes(["600519"]).get("600519")
        assert q and q.price, "未取到 600519 报价"
        print(f"  [PASS] 实时行情: 600519 {q.name} {q.price} ({q.change_pct}%)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 实时行情: {exc}")
    try:
        kls = ctx.get_kline("600519", "day", 30)
        assert len(kls) >= 20, f"K线数量不足: {len(kls)}"
        print(f"  [PASS] K线(自动降级): 600519 {len(kls)}根, 最新 {kls[-1].date} 收 {kls[-1].close}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] K线: {exc}")
    try:
        rows = ctx._market_fetch(100)
        assert rows, "全市场列表为空"
        print(f"  [PASS] 全市场快照: 首条 {rows[0].get('name')} ({rows[0].get('code')})")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 全市场快照: {exc}")
    try:
        minute = ctx.tencent.get_minute("600519")
        assert minute.points, "分时为空"
        print(f"  [PASS] 腾讯分时: {len(minute.points)}个点, 昨收 {minute.prev_close}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 腾讯分时: {exc}")
    try:
        news = ctx.em.get_fast_news(5)
        print(f"  [PASS] 7x24快讯: {len(news)}条, 首条: {news[0].title[:30] if news else '-'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 7x24快讯: {exc}")

    print("\n[4] 数据目录")
    print(f"  配置/数据目录: {data_dir()}")
    print("=" * 60)
    print("自检" + ("通过" if ok else "存在失败项"))
    return 0 if ok else 1


def app_main() -> None:
    parser = argparse.ArgumentParser(prog="StockPilot", description=APP_NAME)
    parser.add_argument("--smoke", action="store_true", help="运行自检后退出")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    setup_logging()

    from .crashguard import install as install_crashguard
    install_crashguard()          # 闪退防线：未捕获异常/Qt fatal 全部落 crash.log

    if args.smoke:
        sys.exit(smoke())

    from .ui.app import run_app
    run_app()


if __name__ == "__main__":
    app_main()
