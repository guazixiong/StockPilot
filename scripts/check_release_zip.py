# -*- coding: utf-8 -*-
"""发布防泄闸门（v7.2.9 事故后固化）：Release zip 上传前必须跑本脚本。

用法：
    python scripts/check_release_zip.py <zip路径> [--allow-extra NAME]*
退出码 0=可发布，1=发现泄露/非白名单文件。

背景：v7.2.9 曾把含真实 API Key 的 AI 配置文件打进便携包并公开上传。
规则：
  1) zip 文件清单只允许：主程序 exe、AI 配置模板、使用说明。
  2) 全部文件内容扫描密钥形态（sk- key、飞书/钉钉 webhook、内网地址）。
  3) AI 配置必须是空 profiles 模板。
  4) 禁止携带运行时数据（config.json/kline.db/日志/sessions）。
"""
from __future__ import annotations

import json
import re
import sys
import zipfile

ALLOWED_BASE = {
    "StockPilot/StockPilot.exe",
    "StockPilot/StockPilot-AI配置.json",
    "StockPilot/使用说明.txt",
}

BANNED_CONTENT = re.compile(
    rb"sk-[A-Za-z0-9]{16,}"
    rb"|open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-f-]{20,}"
    rb"|access_token=[A-Za-z0-9]{20,}"
    rb"|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:9\d{3}"
)
BANNED_NAMES = re.compile(
    r"kline\.db$|/config\.json$|app\.log$|crash\.log$|/sessions|/logs/"
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    zip_path = argv[1]
    allowed = set(ALLOWED_BASE)
    for extra in argv[argv.index("--allow-extra") + 1:] if "--allow-extra" in argv else []:
        allowed.add(extra)

    fails: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        print("== 清单白名单 ==")
        for n in names:
            banned = (n not in allowed) or BANNED_NAMES.search(n)
            tag = "!! 拒绝" if banned else "OK"
            print(f"  {tag:8s} {n}")
            if banned:
                fails.append(f"非白名单/禁运文件: {n}")
        print("== 内容密钥扫描 ==")
        for n in names:
            data = z.read(n)
            m = BANNED_CONTENT.search(data)
            if m:
                fails.append(f"{n} 含密钥形态: {m.group()[:40]!r}")
                print(f"  !! 泄露  {n}: {m.group()[:40]}")
            else:
                print(f"  clean    {n}")
        ai_name = "StockPilot/StockPilot-AI配置.json"
        if ai_name in names:
            cfg = json.loads(z.read(ai_name).decode("utf-8"))
            if cfg.get("ai", {}).get("profiles"):
                fails.append("AI 配置含 profiles（必须为空模板）")
    print()
    if fails:
        print("RESULT: REJECTED")
        for f in fails:
            print("  -", f)
        return 1
    print("RESULT: SAFE-TO-PUBLISH")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
