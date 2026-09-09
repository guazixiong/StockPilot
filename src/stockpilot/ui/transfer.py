"""一键导入/导出：AI 配置（JSON）、自选股（CSV）、持仓（CSV）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from ..core.export_csv import rows_to_csv  # noqa: F401 统一走 utf-8-sig

CSV_HEADERS_WATCH = ["code", "name"]
CSV_HEADERS_POS = ["code", "name", "cost", "qty", "date", "strategy"]


def _pick_save(parent, default: str, kinds: str) -> Optional[Path]:
    from PySide6.QtWidgets import QFileDialog
    path, _ = QFileDialog.getSaveFileName(parent, "导出到…", default, kinds)
    return Path(path) if path else None


def _pick_open(parent, kinds: str) -> Optional[Path]:
    from PySide6.QtWidgets import QFileDialog
    path, _ = QFileDialog.getOpenFileName(parent, "从…导入", "", kinds)
    return Path(path) if path else None


# ---------------------------------------------------------------- AI 配置
def export_ai_config(parent, cfg) -> Tuple[bool, str]:
    """AI 配置导出为 JSON（不含 API Key 明文？用户本地工具——按原样含 Key，
    文件即密钥，提示里说明）。"""
    p = _pick_save(parent, "StockPilot-AI配置.json", "JSON (*.json)")
    if not p:
        return False, "已取消"
    data = {"ai": cfg.ai, "_note": "StockPilot AI 配置导出（含 API Key，请妥善保管）"}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, f"已导出 AI 配置到 {p}"


def import_ai_config(parent, cfg) -> Tuple[bool, str]:
    p = _pick_open(parent, "JSON (*.json)")
    if not p:
        return False, "已取消"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"导入失败：{exc}"
    ai = data.get("ai")
    if not isinstance(ai, dict):
        return False, "文件格式不对（缺少 ai 节）"
    cfg.data["ai"] = ai
    cfg.save()
    return True, f"已导入 {len(ai.get('profiles') or {})} 套 AI 配置"


# ---------------------------------------------------------------- 自选股
def export_watchlist(parent, cfg) -> Tuple[bool, str]:
    p = _pick_save(parent, "自选股.csv", "CSV (*.csv)")
    if not p:
        return False, "已取消"
    wl = cfg.get_watchlist()
    rows = [{"code": x.get("code"), "name": x.get("name")} for x in wl]
    from ..core.export_csv import rows_to_csv as _w
    _w(str(p), ["code", "name"], rows)
    return True, f"已导出 {len(rows)} 只自选到 {p}"


def import_watchlist(parent, cfg) -> Tuple[bool, str]:
    import csv as _csv
    p = _pick_open(parent, "CSV (*.csv)")
    if not p:
        return False, "已取消"
    added = dup = 0
    try:
        with open(p, encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if len(code) == 6 and code.isdigit():
                    if cfg.add_watch(code, name):
                        added += 1
                    else:
                        dup += 1
    except OSError as exc:
        return False, f"导入失败：{exc}"
    return True, f"导入完成：新增 {added} 只，重复跳过 {dup} 只"


# ---------------------------------------------------------------- 持仓
def export_positions(parent, cfg) -> Tuple[bool, str]:
    p = _pick_save(parent, "持仓.csv", "CSV (*.csv)")
    if not p:
        return False, "已取消"
    rows = []
    for code, pos in (cfg.monitor.get("positions") or {}).items():
        rows.append({"code": code, "name": pos.get("name"),
                    "cost": pos.get("cost"), "qty": pos.get("qty"),
                    "date": pos.get("date"), "strategy": pos.get("strategy")})
    from ..core.export_csv import rows_to_csv as _w
    _w(str(p), ["code", "name", "cost", "qty", "date", "strategy"], rows)
    return True, f"已导出 {len(rows)} 条持仓到 {p}"


def import_positions(parent, cfg) -> Tuple[bool, str]:
    import csv as _csv
    p = _pick_open(parent, "CSV (*.csv)")
    if not p:
        return False, "已取消"
    n = bad = 0
    try:
        with open(p, encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                code = (row.get("code") or "").strip()
                if len(code) != 6 or not code.isdigit():
                    bad += 1
                    continue
                try:
                    info = {
                        "code": code,
                        "name": (row.get("name") or "").strip(),
                        "cost": float(row.get("cost") or 0),
                        "qty": int(float(row.get("qty") or 0)),
                        "date": (row.get("date") or "").strip(),
                        "strategy": (row.get("strategy") or "").strip(),
                    }
                except ValueError:
                    bad += 1
                    continue
                if info["cost"] <= 0 or info["qty"] <= 0:
                    bad += 1
                    continue
                cfg.save_position(code, info)
                n += 1
    except OSError as exc:
        return False, f"导入失败：{exc}"
    return True, f"导入完成：{n} 条持仓，无效跳过 {bad} 条"


from typing import Optional  # noqa: E402


# ---------------------------------------------------------------- 策略库
def export_strategies(parent, cfg) -> Tuple[bool, str]:
    """自定义策略库导出 JSON（内置策略不导——目标机自带）。"""
    st = cfg.get_strategy_dicts()
    if not st:
        return False, "暂无自定义策略可导出"
    p = _pick_save(parent, "StockPilot-策略库.json", "JSON (*.json)")
    if not p:
        return False, "已取消"
    p.write_text(json.dumps({"strategies": st}, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return True, f"已导出 {len(st)} 套自定义策略到 {p}"


def import_strategies(parent, cfg) -> Tuple[bool, str]:
    p = _pick_open(parent, "JSON (*.json)")
    if not p:
        return False, "已取消"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"导入失败：{exc}"
    st = data.get("strategies")
    if not isinstance(st, list):
        return False, "文件格式不对（缺少 strategies 节）"
    n = skip = 0
    for d in st:
        if isinstance(d, dict) and d.get("name"):
            # 同名覆盖合并，导入选
            cfg.save_strategy_dict(d)
            n += 1
        else:
            skip += 1
    return True, f"导入完成：{n} 套策略，无效跳过 {skip} 套"


# ---------------------------------------------------------------- 选股方案
def export_screen_plans(parent, cfg) -> Tuple[bool, str]:
    plans = cfg.get_plans()
    if not plans:
        return False, "暂无选股方案可导出"
    p = _pick_save(parent, "StockPilot-选股方案.json", "JSON (*.json)")
    if not p:
        return False, "已取消"
    p.write_text(json.dumps({"plans": plans}, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return True, f"已导出 {len(plans)} 个方案到 {p}"


def import_screen_plans(parent, cfg) -> Tuple[bool, str]:
    p = _pick_open(parent, "JSON (*.json)")
    if not p:
        return False, "已取消"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"导入失败：{exc}"
    plans = data.get("plans")
    if not isinstance(plans, list):
        return False, "文件格式不对（缺少 plans 节）"
    n = 0
    for plan in plans:
        if isinstance(plan, dict) and plan.get("name"):
            cfg.save_plan(plan["name"], plan.get("cond") or {},
                          plan.get("tech") or [])
            n += 1
    return True, f"导入完成：{n} 个方案"


# ================================================================ 导入模板（v5.2.1）
# 用户痛点：导入按钮有，但不知道文件长什么样、字段名是什么。
# 每种导入类型提供一份与导入器字段严格一致的示例模板（含说明注释），
# 用户改数据直接导入即可。

def _tpl_ai_config() -> str:
    """AI 配置模板：与 import_ai_config 的 ai 节对齐。"""
    return json.dumps({
        "_note": "StockPilot AI 配置导入模板——把 profiles 里换成你的配置后导入。"
                 "vendor 常用值：deepseek/openai/kimi/qwen/zhipu/openrouter/"
                 "siliconflow/ollama/custom",
        "ai": {
            "vendor": "deepseek",
            "api_key": "sk-你的密钥",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 4096,
            "profiles": {
                "我的DeepSeek": {"vendor": "deepseek", "api_key": "sk-xxx",
                                  "base_url": "https://api.deepseek.com/v1",
                                  "model": "deepseek-chat"}
            },
        },
    }, ensure_ascii=False, indent=2)


def _tpl_watchlist_csv() -> str:
    lines = ["code,name",
             "600519,贵州茅台",
             "300750,宁德时代",
             "510300,沪深300ETF"]
    return chr(10).join(lines)


def _tpl_positions_csv() -> str:
    lines = ["code,name,cost,qty,date,strategy",
             "600519,贵州茅台,1650.00,100,2026-08-01,趋势启动",
             "300750,宁德时代,210.50,200,2026-08-15,回调企稳",
             "（说明）cost=成本价(>0)；qty=股数(>0)；date=建仓日期；strategy=策略名（可空）"]
    return chr(10).join(lines)


def _tpl_strategies() -> str:
    return json.dumps({
        "_note": "自定义策略导入模板——buy_rules/sell_rules 的 indicator 见"
                 "「策略监听」页中文指标下拉（如 现价 price/MA5 ma5/量比 volume_ratio）；"
                 "op 支持 > >= < <= ==；多个规则间 logic=and/or",
        "strategies": [
            {"name": "我的均线策略",
             "desc": "MA5 上穿 MA20 且放量",
             "buy_rules": [{"indicator": "ma5", "op": ">", "value": "ma20",
                            "logic": "and"},
                           {"indicator": "vol_vs_ma5", "op": ">", "value": 1.5,
                            "logic": "and"}],
             "sell_rules": [{"indicator": "price", "op": "<", "value": "ma20",
                             "logic": "and"}],
             "stop_loss_pct": 6.0, "take_profit_pct": 12.0, "max_hold_days": 20},
        ],
    }, ensure_ascii=False, indent=2)


def _tpl_screen_plans() -> str:
    return json.dumps({
        "_note": "选股方案导入模板——cond 可用键（_min/_max 后缀）："
                 "price 现价(元)/change_pct 涨跌幅(%)/turnover_rate 换手率(%)"
                 "/volume_ratio 量比/float_mv 流通市值(亿)/pe 市盈率"
                 "/pb 市净率/amount 成交额(万元)；"
                 "tech 是勾选参与的技术策略名单子集（如 趋势启动/回调企稳）",
        "plans": [
            {"name": "我的低价放量方案",
             "cond": {"price_min": 3, "price_max": 30,
                      "turnover_rate_min": 3, "amount_min": 10000},
             "tech": ["趋势启动"]},
        ],
    }, ensure_ascii=False, indent=2)


TEMPLATES = {
    "ai_config": ("StockPilot-AI配置模板.json", _tpl_ai_config),
    "watchlist": ("自选股导入模板.csv", _tpl_watchlist_csv),
    "positions": ("持仓导入模板.csv", _tpl_positions_csv),
    "strategies": ("StockPilot-策略导入模板.json", _tpl_strategies),
    "screen_plans": ("StockPilot-选股方案模板.json", _tpl_screen_plans),
}


def save_template(parent, kind: str) -> Tuple[bool, str]:
    """保存一份导入模板到用户选择的位置。kind 见 TEMPLATES 键。"""
    if kind not in TEMPLATES:
        return False, f"未知模板类型: {kind}"
    default, maker = TEMPLATES[kind]
    p = _pick_save(parent, default, "模板 (*.json *.csv)")
    if not p:
        return False, "已取消"
    try:
        p.write_text(maker(), encoding="utf-8")
    except OSError as exc:
        return False, f"模板保存失败：{exc}"
    return True, f"模板已保存到 {p}——填入你的数据后即可用「导入」按钮导入"


# ---------------------------------------------------------------- 通用 JSON 导入（v6.3.1）
def import_plans_json(parent, cfg) -> Tuple[bool, str]:
    """从 JSON 文件导入买入计划（学习 v4.11.0 的计划结构，不预置任何标的）。

    支持两种格式：
    A. 本系统导出格式：{"buy_plans": [{code,name,target_amount,
        firstBuyRange:[低,高], addBuyRange:[低,高], doNotChaseAbove}]}
    B. investment-system v4.x 兼容格式：{"buyPlans": [...同上字段...]}
    导入后存 config["monitor"]["buy_plans"]（覆盖式——与 v4.11.0 计划文件一致为唯一真相）。
    """
    p = _pick_open(parent, "JSON (*.json)")
    if not p:
        return False, "已取消"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"导入失败：{exc}"
    raw = data.get("buy_plans") or data.get("buyPlans") or []
    if not isinstance(raw, list) or not raw:
        return False, "文件格式不对（缺少 buyPlans/buy_plans 节）"
    plans = []
    for d in raw:
        try:
            plans.append({
                "code": str(d["code"]),
                "name": str(d.get("name") or d["code"]),
                "target_amount": float(d.get("targetAmount") or d.get("target_amount") or 1000),
                "firstBuyRange": [float(x) for x in d["firstBuyRange"][:2]],
                "addBuyRange": [float(x) for x in d["addBuyRange"][:2]],
                "doNotChaseAbove": float(d["doNotChaseAbove"]),
            })
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    if not plans:
        return False, "没有可识别的计划条目（需要 code/firstBuyRange/addBuyRange/doNotChaseAbove）"
    cfg.monitor["buy_plans"] = plans
    cfg.save()
    return True, f"已导入 {len(plans)} 份买入计划——盘中调度器将自动监控进入区间的标的"


def import_holdings_json(parent, cfg) -> Tuple[bool, str]:
    """从 JSON 文件导入持仓（通用——不预置任何人的真实持仓）。

    支持 investment-system holdings 格式：
    {"holdings": [{code,name,shares,cost}], "available_cash": 数值}
    与通用格式 {"positions": [{code,name,cost,qty,date,strategy}]}。
    """
    p = _pick_open(parent, "JSON (*.json)")
    if not p:
        return False, "已取消"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"导入失败：{exc}"
    raw = data.get("holdings") or data.get("positions") or []
    if not isinstance(raw, list) or not raw:
        return False, "文件格式不对（缺少 holdings/positions 节）"
    n = 0
    for d in raw:
        code = str(d.get("code") or "").strip()
        qty = d.get("shares") or d.get("qty")
        cost = d.get("cost")
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            info = {"code": code,
                    "name": str(d.get("name") or code),
                    "cost": float(cost or 0),
                    "qty": int(float(qty or 0)),
                    "date": str(d.get("date") or ""),
                    "strategy": str(d.get("strategy") or "")}
        except (TypeError, ValueError):
            continue
        if info["cost"] > 0 and info["qty"] > 0:
            cfg.save_position(code, info)
            n += 1
    cash = data.get("available_cash")
    if isinstance(cash, (int, float)) and cash > 0:
        w = cfg.monitor.get("watch") or {}
        w["available_cash"] = float(cash)
        cfg.monitor["watch"] = w
    cfg.save()
    if n == 0 and not isinstance(cash, (int, float)):
        return False, "没有可识别的持仓条目"
    extra = f"；可用现金 ¥{cash:,.0f}" if isinstance(cash, (int, float)) else ""
    return True, f"已导入 {n} 只持仓{extra}"
