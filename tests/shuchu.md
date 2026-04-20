# CoreCoder 输出格式文档

## 概述

CoreCoder 使用 **Rich 库** 实现终端输出格式化，支持颜色、样式、面板、Markdown 等多种输出格式。

## 核心组件

### 1. Console 类

```python
from rich.console import Console

console = Console()
```

### 2. 主要导入

```python
from rich.console import Console    # 控制台输出
from rich.markdown import Markdown  # Markdown 渲染
from rich.panel import Panel        # 面板展示
from rich.text import Text          # 文本对象
```

## 格式语法

### 颜色代码

| 颜色名 | 语法 | 示例 |
|--------|------|------|
| 红色 | `[red]` | `console.print("[red]错误[/red]")` |
| 绿色 | `[green]` | `console.print("[green]成功[/green]")` |
| 蓝色 | `[blue]` | `console.print("[blue]信息[/blue]")` |
| 青色 | `[cyan]` | `console.print("[cyan]提示[/cyan]")` |
| 黄色 | `[yellow]` | `console.print("[yellow]警告[/yellow]")` |
| 紫色 | `[purple]` | `console.print("[purple]重要[/purple]")` |

### 样式代码

| 样式 | 语法 | 说明 |
|------|------|------|
| 粗体 | `[bold]` | 加粗文本 |
| 斜体 | `[italic]` | 斜体文本 |
| 淡化 | `[dim]` | 灰色淡化 |
| 删除线 | `[strike]` | 删除线效果 |
| 下划线 | `[underline]` | 下划线 |

### 组合使用

```python
# 粗体红色
console.print("[bold red]粗体红色文本[/bold red]")

# 青色淡化
console.print("[dim cyan]淡化青色文本[/dim cyan]")
```

## 输出组件

### 1. 面板 Panel

```python
console.print(Panel(
    "内容文本\n第二行内容",
    title="面板标题",
    border_style="blue",
))
```

输出效果：
```
╭─────────────────────────────────────╮
│ 面板标题                           │
├─────────────────────────────────────┤
│ 内容文本                           │
│ 第二行内容                         │
╰─────────────────────────────────────╯
```

### 2. Markdown 渲染

```python
console.print(Markdown("# 标题\n\n**粗体** 和 *斜体*"))
```

输出效果：
```
标题
====

粗体 和 斜体
```

### 3. 普通文本输出

```python
console.print("普通文本")
console.print("[bold]粗体文本[/bold]")
```

## 在 CoreCoder 中的应用

### 启动面板

```python
console.print(Panel(
    f"[bold]CoreCoder[/bold] v{__version__}\n"
    f"Model: [cyan]{config.model}[/cyan]",
    border_style="blue",
))
```

### 状态提示

```python
# 成功
console.print(f"[green]Session saved: {sid}[/green]")

# 错误
console.print("[red bold]No API key found.[/red bold]")

# 警告
console.print("[yellow]Interrupted.[/yellow]")
```

### 工具调用提示

```python
console.print(f"\n[dim]> {name}({kwargs})[/dim]")
```

## Emoji 支持

Rich 支持直接使用 Emoji 表情：

```python
console.print("🧠 编程与开发")
console.print("🔍 代码分析")
console.print("🛠️ 工具与环境")
console.print("📚 学习与教学")
console.print("🎯 具体任务")
```

## 完整示例

```python
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()

# 输出帮助信息
console.print(Panel(
    "[bold]Commands:[/bold]\n"
    "  /help          Show this help\n"
    "  /reset         Clear conversation history\n"
    "  /model         Show current model",
    title="CoreCoder Help",
    border_style="dim",
))

# 输出 Markdown 内容
console.print(Markdown("""
## 功能列表

- 代码编写
- 代码修复
- 代码优化
"""))
```

## 注意事项

1. **样式闭合**：所有样式标签必须正确闭合 `[/]`
2. **终端支持**：确保终端支持真彩色输出
3. **兼容性**：Windows PowerShell 可能需要额外配置
4. **性能**：复杂格式可能影响输出性能，建议适度使用

## 参考资料

- Rich 官方文档: https://rich.readthedocs.io/
- Rich GitHub: https://github.com/Textualize/rich
