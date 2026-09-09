"""AI 厂商预设注册表。新增厂商 = 追加一个 Vendor 条目。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Vendor:
    name: str
    base_url: str
    models: Tuple[str, ...] = ()
    needs_key: bool = True
    note: str = ""


CUSTOM = "自定义"

PRESETS: Tuple[Vendor, ...] = (
    Vendor("DeepSeek", "https://api.deepseek.com/v1",
           ("deepseek-chat", "deepseek-reasoner"),
           note="国内直连，性价比高"),
    Vendor("OpenAI", "https://api.openai.com/v1",
           ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"),
           note="可能需要代理"),
    Vendor("Kimi(月之暗面)", "https://api.moonshot.cn/v1",
           ("moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0711-preview")),
    Vendor("通义千问(百炼)", "https://dashscope.aliyuncs.com/compatible-mode/v1",
           ("qwen-plus", "qwen-turbo", "qwen-max")),
    Vendor("智谱GLM", "https://open.bigmodel.cn/api/paas/v4",
           ("glm-4-flash", "glm-4-plus", "glm-4-air")),
    Vendor("OpenRouter", "https://openrouter.ai/api/v1",
           ("openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet",
            "google/gemini-2.0-flash-001"), note="聚合多家，可能需要代理"),
    Vendor("硅基流动", "https://api.siliconflow.cn/v1",
           ("Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3")),
    Vendor("Ollama(本地)", "http://127.0.0.1:11434/v1",
           ("qwen2.5:7b", "llama3.1:8b"), needs_key=False,
           note="本地部署，无需联网"),
)


def get_vendor(name: str) -> Optional[Vendor]:
    for v in PRESETS:
        if v.name == name:
            return v
    return None


def vendor_names() -> list:
    return [v.name for v in PRESETS] + [CUSTOM]
