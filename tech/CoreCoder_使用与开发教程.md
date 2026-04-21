# CoreCoder 使用与开发教程

本教程面向项目使用者与维护者，帮助你快速上手 CoreCoder，并理解如何扩展它。

---

## 1. 项目是什么

`CoreCoder` 是一个命令行 AI 编码 Agent，核心能力是：

- 接收你的自然语言任务
- 通过工具读取/编辑文件、执行命令
- 多轮迭代直到产出结果
- 支持会话保存、上下文压缩、skills 行为约束

当前项目还增加了：

- Skills 自动加载（项目级 + 用户级）
- 输出净化与结构校验（`/validate-output on|off`）
- 流式显示开关（`/stream on|off`）

---

## 2. 快速开始

### 2.1 安装依赖

在项目根目录执行：

```bash
pip install -e .
```

如果你只想装正式包：

```bash
pip install corecoder
```

### 2.2 配置模型环境变量

常见方式（OpenAI 兼容）：

```bash
export OPENAI_API_KEY=你的key
export OPENAI_BASE_URL=你的接口地址
```

Windows PowerShell 示例：

```powershell
$env:OPENAI_API_KEY="你的key"
$env:OPENAI_BASE_URL="你的接口地址"
```

### 2.3 启动

```bash
corecoder -m gpt-4o
```

单次执行模式：

```bash
corecoder -p "请检查当前项目并总结结构"
```

---

## 3. 常用 REPL 命令

- `/help` 查看帮助
- `/model` 查看或切换模型
- `/tokens` 查看 token 用量
- `/compact` 压缩上下文
- `/save` 保存会话
- `/sessions` 查看会话列表
- `/skills` 查看已加载 skills
- `/validate-output on|off` 开关输出净化与结构校验
- `/stream on|off` 开关流式输出
- `quit` 退出

---

## 4. 输出控制（重点）

### 4.1 `/validate-output on|off`

当 `on` 时，最终输出会经过：

1. 协议标签清理（如 `<tool_call>`、`<function=...>`）
2. 重复段落去重
3. 7 段固定标题校验（不通过会尝试重写一次）

适合：你希望结果稳定、结构化、可交付。

### 4.2 `/stream on|off`

当 `on` 时：

- 先看到原始流式输出（实时）
- 若 `validate-output` 也开启，结束后再显示净化后的总结面板

适合：你希望有实时反馈，又保留最终干净结果。

---

## 5. Skills 机制

### 5.1 目录约定

- 项目级：`.cursor/skills/<skill-name>/SKILL.md`
- 用户级：`~/.cursor/skills/<skill-name>/SKILL.md`

### 5.2 你当前项目已有中文 skills（示例）

- `build-small-project`
- `api-dev-zh`
- `bug-fix-zh`
- `test-coverage-zh`
- `code-review-zh`
- `refactor-zh`

### 5.3 建议写法

一个高质量 skill 应包含：

- 明确目标（做什么）
- 触发场景（何时用）
- 执行步骤（怎么做）
- 输出格式（如何汇报）
- 反幻觉与证据规则（如何避免假完成）

---

## 6. 推荐测试流程（可直接复制）

### 6.1 检查 skills 是否加载

```text
/skills
```

### 6.2 开启净化与流式

```text
/validate-output on
/stream on
```

### 6.3 发起任务验证（综合）

```text
请对 demo/todo_app 做综合总结，严格按7段标题输出，每个已完成结论必须附文件或命令证据。
```

### 6.4 对比 raw 模式

```text
/validate-output off
请对 demo/todo_app 做综合总结，严格按7段标题输出。
```

观察点：

- `on` 时输出更干净、重复更少、结构更稳定
- `off` 时更接近模型原始输出

---

## 7. 二次开发入口

### 7.1 关键文件

- `corecoder/cli.py`：REPL 入口、命令处理、输出控制
- `corecoder/agent.py`：Agent 循环、工具执行、并发工具调用
- `corecoder/llm.py`：模型调用与流式输出
- `corecoder/prompt.py`：系统提示词
- `corecoder/skills.py`：skills 发现与注入
- `corecoder/tools/`：工具实现（bash/read/write/edit/glob/grep/agent）

### 7.2 常见扩展方向

- 新增自定义工具（例如 HTTP、数据库查询）
- 增加 output validator 规则（更强结构校验）
- 加入阶段状态机（让“协作编排”从软规则变硬规则）
- 增加命令开关（如 `/validate-output diff`）

---

## 8. 常见问题

### Q1：新命令不生效？

A：通常是旧进程没重启。退出后重新启动 `corecoder`。

### Q2：`/stream` 为什么看不到效果？

A：只有在模型输出 token 时可见。若回复很短或直接返回，体感不明显。

### Q3：为什么 on/off 看起来差不多？

A：当模型原始输出本身已经规整时，净化器不会改很多内容，这是正常现象。

---

## 9. 团队使用建议

- 日常开发：`/stream on` + `/validate-output on`
- 排查问题：切到 `/validate-output off` 看 raw 输出
- 上线前：使用固定提示词做回归压测，检查结构、证据、重复和安全约束

---

如需继续完善，建议下一步新增 `tech/压测脚本与验收标准.md`，把“通过/失败”判定标准固化成团队规范。
