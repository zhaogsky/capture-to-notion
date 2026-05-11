# Capture to Notion

Capture to Notion 是一个 Claude Code Skill，以及与之配套的本地 CLI 后端，用于规划并执行写入 Notion 的操作。它的范围刻意限定在内容采集工作流：选择目标、扫描结构、生成写入计划、解析关联关系、处理封面素材，并在用户确认后执行写入。

## 组成

- `SKILL.md` — 面向 Claude 的工作流与安全规则。
- `capture_to_notion/` — CLI 使用的 Python 后端。
- `tests/` — 覆盖计划生成、目标扫描、写入、素材处理和 CLI 行为的回归测试。
- `pyproject.toml` — 包元数据和 `capture-to-notion` 命令入口。

## 安装或重装

在当前目录以 editable 方式安装 CLI：

```bash
uv tool install --force --editable /Users/aaron/.claude/skills/capture-to-notion
```

验证命令是否可用：

```bash
capture-to-notion --help
```

## 配置

默认本地配置目录：

```text
~/.config/capture-to-notion/
```

测试或隔离运行时可以覆盖配置目录：

```bash
CAPTURE_TO_NOTION_CONFIG_DIR=/tmp/capture-to-notion capture-to-notion cache inspect
```

Notion integration token 应配置在这个工具自己的本地配置中，不要写入 Claude Code 全局 settings。不要把密钥放进 Skill 目录。

## 诊断命令

输出版本信息、包路径和运行时路径信息：

```bash
capture-to-notion version
```

运行只读本地诊断，检查配置路径、是否已配置 token，以及是否存在旧配置目录提示。该命令不会输出 token 原文：

```bash
capture-to-notion doctor
```

迁移历史和重命名说明见 `CHANGELOG.md`。

## 常用命令

查看本地缓存：

```bash
capture-to-notion cache inspect
```

查看本地缓存的 Notion 目标，不访问 Notion：

```bash
capture-to-notion target list
```

按 alias 查看一个本地缓存目标的结构：

```bash
capture-to-notion target inspect --alias books
```

搜索目标页面或数据库：

```bash
capture-to-notion target search --query "书单"
```

扫描已确认的目标：

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

创建写入计划：

```bash
capture-to-notion capture plan --input input.json --output plan.json
```

执行已确认的计划：

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

## 典型工作流

1. 搜索或选择精确的 Notion 目标。
2. 首次使用目标前，或目标结构变化后，先扫描目标。
3. 构造 input JSON，包含原始内容、目标提示、状态、内容类型提示和选项。
4. 使用 `capture-to-notion capture plan` 生成计划。
5. 审阅计划和警告。
6. 只有在用户确认目标和写入内容后，才执行 apply。

## 安全边界

Capture to Notion 在这个工作流中替代 Notion MCP。处理扫描、计划、写入、验证或结构读取时，不要回退使用 Notion MCP。如果后端缺少某个 API 操作，或返回过期数据，应优先修复或扩展这个 Skill/后端；只有用户明确要求一次性的 MCP 操作时，才使用 Notion MCP。

这个工具不能静默写入 Notion。首次使用目标，以及任何需要确认的计划，都应保持“先展示计划，确认后再写入”。

## 测试

在 Skill 目录下运行后端测试：

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```
