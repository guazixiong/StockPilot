"""AppContext：跨页面共享的配置、数据源、AI 客户端工厂。"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..core.ai.client import AiConfig, OpenAIClient
from ..core.models import Quote
from ..core.providers.base import HttpClient, ProviderError
from ..core.providers.eastmoney import EastmoneyProvider
from ..core.providers.sina import SinaProvider
from ..core.providers.tencent import TencentProvider
from ..core.screener import Screener
from ..core.storage import Config

log = logging.getLogger(__name__)


class AppContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        proxy = (cfg.get("market") or {}).get("proxy") or ""
        self.http = HttpClient(proxy=proxy or None)
        self.tencent = TencentProvider(self.http)
        self.sina = SinaProvider(self.http)
        self.em = EastmoneyProvider(self.http)
        from ..core.kline_store import KLineStore
        self.store = KLineStore()
        from ..core.monitor import MonitorService
        self.monitor = MonitorService(cfg, self, self._market_fetch, self.get_kline)
        self.screener = Screener(self._market_fetch, self.get_kline)
        self._ai_cache: Optional[OpenAIClient] = None

    # ------------------------------------------------------------ 行情
    def get_quotes(self, codes: List[str]) -> Dict[str, Quote]:
        """腾讯主源，新浪备源。"""
        try:
            return self.tencent.get_quotes(codes)
        except ProviderError as exc:
            log.warning("腾讯行情失败，切换新浪: %s", exc)
            return self.sina.get_quotes(codes)

    def kline_for_scan(self, code: str, limit=None, max_age_days: int = 4) -> list:
        """策略扫描专用：缓存优先，但缓存必须"够新且够量"
        （最新K线距今 ≤ max_age_days 个自然日，且根数满足本次请求；
        不足量时在线拉取并回写——v4.3 窗口 320→800 后老缓存需自然升级）。
        足量口径：≥ limit*0.9，或 ≥ 上次在线实得根数（源容量天然不足时
        不反复回源——实得根数记入缓存元数据）。

        v4.4.3 签名兼容：老调用 kline_fetch(code, "day", 800) 把 "day" 当
        limit 传入 → SQLite LIMIT 绑定字符串直接 datatype mismatch（机会
        扫描全灭的真实根因）。limit 参数现在兼容 (code, limit) /
        (code, "day", limit) / (code, period, limit) 三种历史口径。"""
        from datetime import datetime
        if not isinstance(limit, int):
            # (code, "day", 800) 或 (code, period) 形态：向后兼容
            if limit is None:
                limit = 800
            else:
                # 第二参是 period 字符串；真实 limit 在 max_age_days 位
                limit = max_age_days if isinstance(max_age_days, int) else 800
        cached = self.store.get(code, limit)
        if cached and cached[-1].date:
            try:
                newest = datetime.strptime(cached[-1].date, "%Y-%m-%d")
                age = (datetime.now() - newest).days
                enough = len(cached) >= limit * 0.9
                if not enough:   # 源容量天然不足时：根数不再增长即视为足量
                    prev = self.store.fetched_count(code)
                    if prev is not None and len(cached) >= prev:
                        enough = True
                if age <= max_age_days and enough:
                    return cached
            except ValueError:
                pass
        try:
            kls = self.get_kline(code, "day", limit)
        except Exception:  # noqa: BLE001
            return cached or []    # 拉取失败时退回旧缓存（聊胜于无）
        if kls:
            self.store.put(code, kls)
            self.store.record_fetched(code, len(kls))
        return kls

    def warm_market_cache(self, codes: List[str],
                          on_progress=None) -> int:
        """首次全量回填缓存（Sequoia --backfill 等价物）。"""
        return self.store.warm_up(lambda c, p, l: self.get_kline(c, "day", l),
                                  codes, limit=800,
                                  on_progress=on_progress)

    def _market_fetch(self, max_count: int = 2000, on_progress=None) -> List[dict]:
        """全市场快照：东财主源，新浪备源。"""
        try:
            return self.em.fetch_all_market(max_count, on_progress=on_progress)
        except ProviderError as exc:
            log.warning("东财全市场列表失败，切换新浪: %s", exc)
            return self.sina.fetch_all_market(max_count, on_progress=on_progress)

    def all_strategy_names(self) -> List[str]:
        from ..core import strategy as stg
        names = [s.name for s in stg.builtin_strategies()]
        names += [d.get("name") for d in self.cfg.get_strategy_dicts()
                  if d.get("name")]
        return list(dict.fromkeys(names))

    def opportunity_scan(self, rows: List[dict],
                         on_progress=None) -> List:
        """首页/雷达共用的机会扫描：按 config.opportunity 选策略、传评分配置。"""
        from ..core import strategy as stg
        from ..core.monitor import run_strategy_scan
        all_st = list(stg.builtin_strategies())
        for d in self.cfg.get_strategy_dicts():
            try:
                all_st.append(stg.Strategy.from_dict(d))
            except Exception:  # noqa: BLE001
                pass
        names = set(self.cfg.opportunity_strategies(
            [s.name for s in all_st]))
        selected = [s for s in all_st if s.name in names]
        if not selected:      # 防御：名单失效时回退全量
            selected = all_st
        return run_strategy_scan(selected, rows, self.kline_for_scan,
                                 on_progress,
                                 opp_cfg=self.cfg.opportunity)

    def market_top_n(self, n: int = 30) -> List[dict]:
        """按成交额取 Top N（回测股票范围用）。"""
        try:
            _total, rows = self.em.fetch_market_page(1, page_size=min(n * 2, 200))
        except ProviderError:
            rows = self._market_fetch(max(n * 3, 300))
        rows.sort(key=lambda r: r.get("amount") or 0, reverse=True)
        return rows[:n]

    def get_market_moneyflow(self):
        """沪深两市主力净额（元）。东财点查 → 限流时返回 None（UI 显示提示）。"""
        try:
            return self.em.get_market_moneyflow()
        except ProviderError as exc:
            log.warning("大盘资金流获取失败: %s", exc)
            return None

    def get_industry_boards(self):
        """行业板块：东财（被限流中）→ 新浪主用。"""
        return self.sina.get_industry_boards()

    def get_kline(self, code: str, period: str = "day", limit: int = 250) -> list:
        """K线降级链：东财 → 腾讯 → 新浪。"""
        errors = []
        for provider in (self.em, self.tencent, self.sina):
            try:
                return provider.get_kline(code, period, limit)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise ProviderError(f"全部K线源失败 {code} -> " + " | ".join(errors))

    def suggest(self, keyword: str, count: int = 10):
        """搜索联想降级链：新浪（ETF+股票，稳定）→ 东财。"""
        try:
            items = self.sina.suggest(keyword, count)
            if items:
                return items
        except ProviderError as exc:
            log.warning("新浪联想失败，切换东财: %s", exc)
        return self.em.suggest(keyword, count)

    # ------------------------------------------------------------ AI
    def ai_config(self) -> Optional[AiConfig]:
        prof = self.cfg.active_ai_profile()
        if not prof or not prof.get("base_url") or not prof.get("model"):
            return None
        return AiConfig.from_dict(prof)

    def ai_client(self) -> OpenAIClient:
        cfg = self.ai_config()
        if cfg is None:
            raise RuntimeError("AI 未配置：请先在「设置」页完成 AI 服务配置")
        proxy = (self.cfg.get("market") or {}).get("proxy") or ""
        self._ai_cache = OpenAIClient(cfg, proxy=proxy or "")
        return self._ai_cache
