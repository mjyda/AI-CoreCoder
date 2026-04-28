# CoreCoder

[![PyPI](https://img.shields.io/pypi/v/corecoder)](https://pypi.org/project/corecoder/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/he-yufeng/CoreCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/he-yufeng/CoreCoder/actions)

[中文](README_CN.md) | [English](README.md) | [Architecture Notes](article/)

CoreCoder is a **natural-language personal assistant framework** built around a coding agent core, a supervisor-style multi-agent layer, and a pluggable action/execution system.

This repository is no longer just a minimal coding-agent demo. It has evolved into a project for building an assistant that can:

- understand natural language requests,
- route work across multiple experts,
- compose multi-step execution plans dynamically,
- call local tools safely inside a configurable sandbox,
- persist short-term and semantic context,
- and execute cross-domain tasks such as browser -> transform -> file/mail.

## What This Project Is

CoreCoder currently combines two layers:

1. **Base coding agent runtime**
   - LLM wrapper
   - tool calling
   - context compression
   - session save/resume
   - CLI / REPL

2. **SuperAssistant orchestration layer**
   - supervisor + experts
   - semantic slot parsing
   - dynamic planning via capability graph
   - pluggable step executors
   - confirmation / clarification / risk control
   - persistent session and semantic memory

## Current Experts

The repository currently includes 4 expert roles:

- `browser`: browser history and site-oriented tasks
- `mail`: list/search/read/send/reply/delete-style mail workflows
- `file`: file reading, writing, rewriting, formatting, exporting
- `coding`: general coding and codebase operations

In practice, the most actively orchestrated cross-expert workflows today are:

- `browser -> file`
- `browser -> mail`
- `file -> mail`
- `mail -> file`
- multi-target output such as `mail + local file`

## Current Capabilities

Highlights already implemented in the current codebase:

- semantic slot parsing for `intent / source / transform / target / constraints`
- dynamic execution plan staging with `confirm / modify / cancel`
- BFS-based capability graph path search
- multi-hop planning such as `source -> transform -> target`
- multi-target execution
- multi-format export (`json`, `text`, `markdown`)
- multi-recipient and multi-email delivery modes
- session persistence and semantic memory hints
- debug views for planners, actions, and path search
- configurable sandbox root via `.env`

## Project Structure

The most important directories are:

```text
corecoder/
├── cli.py                    REPL and user-facing commands
├── runtime/                  base agent runtime pieces
├── platform/                 config and MCP-related integration
├── multi_agent/              supervisor, planners, mixins, session context
├── tools/                    file/shell/search/mail/browser-facing tools
├── article/                  architecture notes and long-form docs
├── scripts/                  helper and verification scripts
└── tests/                    test assets and future test coverage
```

Within the orchestration layer:

```text
corecoder/multi_agent/
├── __init__.py               SuperAssistant entry and orchestration shell
├── plan_engine.py            semantic planning, BFS path search, step execution
├── session_context.py        session memory and semantic memory persistence
├── multi_agent_browser.py    browser expert logic
├── multi_agent_mail.py       mail expert logic
└── multi_agent_file.py       file expert logic
```

## Quick Start

Install:

```bash
pip install corecoder
```

Or run locally in this repo with your `.env`.

Minimal configuration example:

```env
OPENAI_API_KEY=local
OPENAI_BASE_URL=http://localhost:8000/v1
CORECODER_MODEL=qwen3-coder-30b
CORECODER_SANDBOX_ROOT=D:\corecodertest
```

Run:

```bash
corecoder --super-assistant
```

## Example Prompts

```text
把最近5条浏览记录保存在本地，文件名字为test1

将文件test1发送给xxx@qq.com

把最近5条浏览记录以json和文本格式分别发送给a@qq.com和b@qq.com

把最近5封邮件逐封概括后导出到文件
```

## Useful Commands

```text
/help
/debug
/sandbox
/context
/show-file-tools
/show-mappings
/reload-mappings
```

## Configuration

Core configuration is driven by `.env`.

Important variables:

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

## Roadmap Direction

The repository is trending toward a more general personal super-assistant architecture:

- richer semantic parsing
- more pluggable actions and tools
- temporary/generated capabilities created by the coding expert
- clearer expert boundaries
- stronger evaluation and regression coverage

## License

MIT.
