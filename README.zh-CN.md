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

生成的计划会包含顶层 `summary` 区块，用于在任何写入前快速审阅。重点检查 `target_page`、`target_data_source`、`state`、`mapped_fields`、`key_fields`、`asset_actions`、`requires_confirmation` 和 `warnings`。书籍采集里出现 `book_key_values_missing` 表示作者、ISBN 或页数等关键元数据缺失，需要先确认或补全后再 apply。

## Parser Profile 与字段来源

目标缓存可以在 target 层或 data source 层定义 `parser_profile`。data source 层会覆盖 target 层。默认 book profile 只提供 required/review 字段列表，不会添加业务标签。目标扫描只记录 Notion 字段名和官方字段类型；除非 parser profile 或显式映射提供对应 key，否则不会根据字段名推断业务 record key。

使用 `labels` 和 `title_patterns` 控制 raw input 如何解析为 normalized record。`required_schema_fields` 表示写入计划继续前必须映射到 Notion schema 的 record key；`required_value_fields` 表示计划中必须提取到值的 record key；`summary_key_fields` 表示需要出现在计划审阅摘要中的字段；`trusted_field_sources` 表示哪些字段映射来源可以无需确认地满足 required schema 字段；`asset_trust_required_fields` 表示哪些素材类 record key 在规划附件操作前也必须经过可信来源校验。

`field_sources` 记录每个缓存字段映射的来源。当 `field_sources` 存在时，带有 `trusted_field_sources` profile 的 required mapping 只有来源在该列表中时才可信；其他来源会通过 `untrusted_field_mapping`、`*_schema_incomplete` 或 `*_key_values_missing` 等 warning 触发确认。`asset_trust_required_fields` 会复用同一套可信来源判断，未通过信任校验的素材映射会在生成 asset operation 之前被移除。默认 book profile 信任 `explicit` 和 `profile`，并要求 `cover` 的素材映射也必须可信。planner 不根据 Notion 字段名推断业务字段。

```json
{
  "parser_profile": {
    "book": {
      "labels": {
        "author": ["作者", "author"],
        "isbn": ["ISBN", "isbn"],
        "page_count": ["页数", "pages"]
      },
      "required_schema_fields": ["cover", "author", "isbn", "page_count", "state"],
      "required_value_fields": ["author", "isbn", "page_count"],
      "summary_key_fields": ["cover", "author", "isbn", "page_count"],
      "trusted_field_sources": ["explicit", "profile"],
      "asset_trust_required_fields": ["cover"]
    }
  },
  "data_sources": {
    "books": {
      "fields": {
        "author": "作者",
        "isbn": "ISBN"
      },
      "field_sources": {
        "author": "profile",
        "isbn": "explicit"
      }
    }
  }
}
```

执行已确认的计划：

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

只读验证已写入页面：

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

`capture verify` 会返回包含 `verified`、`checks` 和 `warnings` 的 JSON；在没有计划或显式 mapping 时，它只检查页面是否存在和页面 cover URL 可访问性，不会根据字段名猜测标题、状态、作者、ISBN、页数或封面字段。它不会写入 Notion，也不会下载图片。`capture apply` 在写入结果返回页面 ID 时，会根据 write plan 和 target cache 中的字段映射附加顶层 `verification` 摘要，包含写入字段的验证结果和警告，且不会因为验证警告而隐藏 apply 结果。

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
