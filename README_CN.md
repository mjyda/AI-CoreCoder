# CoreCoder

[English](README.md) | [中文](README_CN.md) | [架构文章](article/)

[![PyPI](https://img.shields.io/pypi/v/corecoder)](https://pypi.org/project/corecoder/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/he-yufeng/CoreCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/he-yufeng/CoreCoder/actions)

CoreCoder 现在更适合被理解为一个**面向自然语言任务的个人助手框架**：它以编程 Agent 为底座，在上面叠加了 Supervisor 多智能体编排、语义槽位解析、动态执行规划、文件沙箱、安全确认和会话记忆。

它已经不只是最初那个“精简版 coding agent”项目，而是在朝着“个人超级助手”方向演进。

## 这个项目现在是什么

当前项目大致分成两层：

1. **基础 Agent 运行层**
   - LLM 封装
   - 工具调用
   - 上下文压缩
   - 会话保存/恢复
   - CLI / REPL

2. **SuperAssistant 编排层**
   - Supervisor + 多专家
   - 语义槽位解析
   - 能力图 + BFS 路径搜索
   - 动态执行草案
   - 确认 / 修改 / 取消
   - 语义记忆与会话持久化

## 当前已有的专家智能体

当前代码里有 4 个专家角色：

- `browser`：浏览器专家
- `mail`：邮件专家
- `file`：文件专家
- `coding`：编程专家

其中这段时间重点打通、已经能做跨专家编排的，主要是：

- 浏览器
- 邮件
- 文件

## 当前已经实现的关键能力

目前项目里已经落地的能力包括：

- 自然语言解析为 `intent / source / transform / target / constraints`
- 动态执行草案与二次确认
- 基于能力图的 BFS 最短路径搜索
- 多跳链路编排（`source -> transform -> target`）
- 多目标执行
- 多格式输出（`json / text / markdown`）
- 多收件人、多封邮件/合并邮件模式
- 浏览器 -> 文件 / 邮件，文件 -> 邮件 等跨专家链路
- 会话持久化与语义记忆
- `/debug` 查看 planners / actions / 路径搜索
- 可配置沙箱目录

## 项目结构

核心目录如下：

```text
corecoder/
├── cli.py                    REPL 与命令入口
├── runtime/                  基础 agent 运行层
├── platform/                 配置与平台集成
├── multi_agent/              多智能体编排、规划、上下文
├── tools/                    文件、Shell、搜索、邮件等工具
├── article/                  架构说明与文章
├── scripts/                  脚本与自检
└── tests/                    测试与测试资产
```

编排核心在：

```text
corecoder/multi_agent/
├── __init__.py               SuperAssistant 主入口
├── plan_engine.py            语义规划、BFS 搜索、步骤执行
├── session_context.py        会话上下文与语义记忆
├── multi_agent_browser.py    浏览器专家逻辑
├── multi_agent_mail.py       邮件专家逻辑
└── multi_agent_file.py       文件专家逻辑
```

## 快速开始

安装：

```bash
pip install corecoder
```

或者直接在仓库中配好 `.env` 后运行。

最小配置示例：

```env
OPENAI_API_KEY=local
OPENAI_BASE_URL=http://localhost:8000/v1
CORECODER_MODEL=qwen3-coder-30b
CORECODER_SANDBOX_ROOT=D:\corecodertest
```

启动：

```bash
corecoder --super-assistant
```

## 示例请求

```text
把最近5条浏览记录保存在本地，文件名字为test1

将文件test1发送给xxx@qq.com

把最近5条浏览记录以json和文本格式分别发送给a@qq.com和b@qq.com

把最近5封邮件逐封概括后导出到文件
```

## 常用命令

```text
/help
/debug
/sandbox
/context
/show-file-tools
/show-mappings
/reload-mappings
```

## 配置方式

项目主要通过 `.env` 配置。

比较重要的变量有：

- `CORECODER_MODEL`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `CORECODER_SANDBOX_ROOT`
- `CORECODER_SESSION_PERSIST`
- `CORECODER_SESSION_PATH`
- `CORECODER_EMAIL_ADDRESS`
- `CORECODER_EMAIL_APP_PASSWORD`
- `CORECODER_IMAP_HOST`
- `CORECODER_SMTP_HOST`

## 当前演进方向

项目现在的方向已经比较明确：

- 更强的自然语言理解
- 更可插拔的 action / tool 体系
- 由编程专家生成临时能力
- 更清晰的专家边界
- 更完善的评测与回归测试

## License

MIT。
