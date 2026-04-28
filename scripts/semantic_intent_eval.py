"""Offline semantic-slot evaluation scaffold.

Usage:
  python scripts/semantic_intent_eval.py
"""

from __future__ import annotations

import json
from pathlib import Path


CASES = [
    {
        "nl": "把最近5封邮件的概括内容导出到文件 D:\\corecodertest\\mail_export_test1.txt",
        "expect": {"source": "mail", "transform": "summarize", "target": "file"},
    },
    {
        "nl": "将浏览器前五条记录发送给458563440@qq.com，主题别睡了",
        "expect": {"source": "browser", "target": "mail"},
    },
    {
        "nl": "把 D:\\corecodertest\\a.txt 发给 2276716701@qq.com",
        "expect": {"source": "file", "target": "mail"},
    },
]


def main() -> None:
    out = {
        "note": "This is a baseline semantic eval set. Wire it to SuperAssistant._parse_semantic_slots for regression.",
        "cases": CASES,
    }
    p = Path("scripts") / "semantic_intent_eval_cases.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote baseline cases: {p}")


if __name__ == "__main__":
    main()
