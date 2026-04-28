# CoreCoder 新增 Action 开发文档

本文用于指导你在当前“动态编排 + 执行器注册表”架构下，新增一个 action。

## 1. 你要改哪些文件

主要改 `corecoder/plan_engine.py`：

1. 在执行器注册表中注册 action 名
2. 在动态 planner 中让该 action 可被命中
3. 实现 action 对应执行函数

可选改动：

- `corecoder/multi_agent_mail.py` / `multi_agent_file.py` / `multi_agent_browser.py`
  - 如果需要复用领域能力（如摘要函数、解析函数）

## 2. 开发步骤（标准流程）

### 步骤 A：定义 action 名称

先确定统一命名（建议动词+对象）：

- 例如：`summarize_recent`、`archive_file`、`extract_links`

### 步骤 B：注册执行器

在 `_init_execution_plan_engine()` 的 `_step_executor_registry` 增加映射：

- `"<action_name>": self._exec_step_<action_name>`

### 步骤 C：在 planner 中生成该 action

在 `_plan_dynamic_orchestration()` 中根据自然语言信号决定：

- 什么时候把 `source_action` 或 `target_action` 设为新 action
- 对应 `args` 如何构造
- `summary_map` 增加对应描述

### 步骤 D：实现执行器函数

新增方法形态（保持一致）：

```python
def _exec_step_xxx(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
    ...
```

约定：

- 成功返回 `None`
- 失败返回格式化错误字符串（通常 `_format_evidence_result(...)`）
- 关键工具调用要写入 `evidence`
- 若要给后续 step 传数据，写入 `intermediate`

### 步骤 E：风险策略确认

一般无需改代码，系统会按 action 自动推断风险。  
如果你的 action 有强副作用（删除/覆盖/发送），建议：

- 在 plan 中显式设置 `risk="high"`，确保二次确认。

## 3. 最小示例（概念）

目标：新增 `summarize_recent`（邮件最近 N 封摘要）

1. 注册：

- `summarize_recent -> _exec_step_summarize_recent`

2. 规划：

- 当输入包含“概括/总结/摘要/逐封”且 source 是 mail 时，设为 `summarize_recent`

3. 执行：

- `gmail_list_recent` 取 UID 列表
- `gmail_get_content` 拉取正文
- 调用摘要函数生成每封一句话
- 把结果写入 `intermediate["mail_output"]`

## 4. 验收清单

新增 action 后至少验证以下场景：

1. **命中场景**：能触发该 action 并正确出草案
2. **确认闸**：确认前不执行，确认后执行
3. **失败分支**：工具缺失/参数缺失时有可读报错
4. **证据输出**：能看到关键 tool 调用
5. **回归**：旧 action 不受影响

## 5. 调试方法

- 启动后输入 `/debug`，检查 action 是否注册成功
- 看草案中 `steps` 的 `action` 是否为你的新 action
- 看执行输出中的 `证据输出` 是否有预期 tool 调用

## 6. 常见坑

- 只写了执行器，忘了在 planner 里生成 action
- 只写了 planner，忘了注册执行器映射
- `intermediate` key 命名不一致，导致后续 step 读不到数据
- action 有副作用但没抬高风险等级
