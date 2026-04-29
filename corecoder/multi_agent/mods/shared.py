"""Shared structures and routing helpers for multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import TypedDict

from ..session_context import AssistantSessionContext


class AssistantState(TypedDict):
    user_input: str
    route: str
    outputs: dict[str, str]
    final_response: str


@dataclass(frozen=True)
class ExpertProfile:
    name: str
    route_key: str
    system_hint: str
    capabilities: tuple[str, ...]


EXPERTS: tuple[ExpertProfile, ...] = (
    ExpertProfile(
        name="邮件专家",
        route_key="mail",
        system_hint="你只负责邮件信息检索与总结。若超出邮件范围，返回需要转交主管。",
        capabilities=("mail", "notes"),
    ),
    ExpertProfile(
        name="浏览器专家",
        route_key="browser",
        system_hint="你只负责浏览记录和网页链接分析。若工具不足，请明确说明。",
        capabilities=("browser-history", "notes"),
    ),
    ExpertProfile(
        name="编程专家",
        route_key="coding",
        system_hint="你是资深软件工程师，只做代码相关任务，优先给出可执行结果。",
        capabilities=("filesystem", "shell", "code-search"),
    ),
    ExpertProfile(
        name="文件专家",
        route_key="file",
        system_hint="你只负责文件整理、写作、总结与归档。",
        capabilities=("filesystem", "notes"),
    ),
)


_DEFAULT_SITE_MAPPINGS = {
    "query_aliases": {
        "知乎": "zhihu",
        "github": "github",
        "deepseek": "deepseek",
        "学习通": "chaoxing",
        "超星": "chaoxing",
        "chaoxing": "chaoxing",
        "b站": "bilibili",
        "哔哩哔哩": "bilibili",
        "微博": "weibo",
        "微信": "wechat",
        "代码仓库": "github",
        "仓库": "github",
        "开源项目": "github",
        "论文": "arxiv",
        "技术博客": "blog",
    },
    "canonical_query": {
        "学习通": "chaoxing",
        "超星学习通": "chaoxing",
        "超星": "chaoxing",
        "github": "github",
        "知乎": "zhihu",
        "微信": "wechat",
    },
    "strict_domains": {
        "知乎": "zhihu.com",
        "zhihu": "zhihu.com",
        "github": "github.com",
        "gitlab": "gitlab.com",
        "deepseek": "deepseek.com",
        "学习通": "chaoxing.com",
        "超星": "chaoxing.com",
        "chaoxing": "chaoxing.com",
        "b站": "bilibili.com",
        "哔哩哔哩": "bilibili.com",
        "微博": "weibo.com",
        "微信": "weixin.qq.com",
        "x": "x.com",
        "twitter": "x.com",
    },
}


def load_site_mappings() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "browser_site_mappings.json"
    if not cfg_path.exists():
        return _DEFAULT_SITE_MAPPINGS
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return _DEFAULT_SITE_MAPPINGS

    merged = {
        "query_aliases": dict(_DEFAULT_SITE_MAPPINGS["query_aliases"]),
        "canonical_query": dict(_DEFAULT_SITE_MAPPINGS["canonical_query"]),
        "strict_domains": dict(_DEFAULT_SITE_MAPPINGS["strict_domains"]),
    }
    for key in merged:
        value = data.get(key)
        if isinstance(value, dict):
            merged[key].update({str(k): str(v) for k, v in value.items()})
    return merged


SITE_MAPPINGS = load_site_mappings()


def route_intent(user_input: str, ctx: AssistantSessionContext | None = None) -> str:
    text = user_input.lower()
    if any(k in text for k in ("邮件", "gmail", "mail")):
        return "mail"
    if ctx:
        if ctx.last_file_path and ctx.last_route == "browser":
            if any(k in user_input for k in ("刚才", "刚刚", "上面", "这个文件", "保存的", "写入的")) and any(
                k in user_input for k in ("内容", "格式", "json", "合法", "修正", "改成", "读取", "验证")
            ):
                return "file"
        if ctx.last_route == "file" and len(user_input.strip()) < 40:
            if any(k in user_input for k in ("继续", "同样", "再来一遍", "再试")):
                return "file"
        if ctx.last_file_path and ctx.last_route == "file":
            if any(k in user_input for k in ("继续", "再保存", "覆盖", "重写")):
                return "file"
        if ctx.last_file_path and ctx.last_file_path.lower().endswith(".json"):
            if len(user_input.strip()) < 72 and any(
                k in user_input for k in ("合法", "标准json", "json格式", "格式化", "美化", "修正内容")
            ):
                if "浏览" not in text and "chrome" not in text:
                    return "file"
    if any(k in text for k in ("浏览", "chrome", "history", "网页", "链接", "站点", "记录", "网址")):
        return "browser"
    norm = text.replace("\\\\", "\\")
    if "corecodertest" in norm or ".json" in text or ".txt" in text:
        if any(
            k in user_input
            for k in ("读取", "验证", "格式", "重写", "修正", "改成", "保存", "写入", "刚刚", "内容", "合法", "文件", "test1")
        ):
            return "file"
    if "json" in text and any(k in user_input for k in ("内容", "格式", "合法", "修正", "改成")):
        return "file"
    if any(k in text for k in ("文档", "文件", "总结", "简报", "笔记")):
        return "file"
    return "coding"
