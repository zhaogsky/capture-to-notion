# Capture to Notion

[English version](README.en.md)

Capture to Notion 是一个 Claude Code Skill 加本地 CLI 后端，用来把内容安全、可审查地写入 Notion。它负责目标选择、结构扫描、缓存优先规划、关系与人员字段解析、封面/文件资产上传、写入执行和写后验证。

这个项目的核心原则是：先生成确定性的预检和写入计划，再由用户确认后执行写入；不要静默写 Notion，也不要在这个流程里回退使用 Notion MCP。

## 功能概览

- 目标发现与扫描：搜索 Notion 页面/数据库，扫描目标结构并缓存 schema、视图、路径等事实。
- 写入前预检：根据输入、目标 hint、缓存和 profile 判断下一步动作。
- 写入计划：生成可审查的 plan，列出目标、字段映射、待写页面、警告和验证预期。
- 关系和人员字段：解析 relation / people，阻断未解决或有歧义的写入。
- 文件资产上传：支持把封面、作者图片等 Notion `files` 字段作为上传实体处理，而不是只保存外链 URL。
- 写后验证：按计划预期检查页面、字段、文件、封面和可验证的视图约束。
- Claude Code Skill 编排：`SKILL.md` 提供面向 Claude 的工作流和安全规则。

## 项目结构

- `SKILL.md`：Claude Code 使用的工作流、安全边界和确认规则。
- `capture_to_notion/`：`capture-to-notion` CLI 的 Python 后端。
- `tests/`：扫描、规划、写入、资产、验证和 CLI 行为的回归测试。
- `pyproject.toml`：包信息和 `capture-to-notion` 命令入口。
- `README.en.md`：英文说明。

## 当前状态

截至 2026-06-03，当前支持的核心工作流已经进入回归维护状态。已有测试覆盖 cache-first 规划、compact 输出、目标路径展示、关系/人员字段安全、文件资产上传、apply 安全校验和写后验证等能力。

后续新增功能建议按真实使用反馈拆成小任务推进，而不是一次性扩展大范围逻辑。

## 安装

在项目目录中安装可编辑 CLI：

```bash
uv tool install --force --editable .
```

验证命令是否可用：

```bash
capture-to-notion --help
```

查看版本和运行路径：

```bash
capture-to-notion version
```

## 配置

默认本地配置目录：

```text
~/.config/capture-to-notion/
```

Notion integration token 应放在工具自己的本地配置中，不要写入 Claude Code 全局 settings，也不要提交到仓库。

可以用环境变量为测试或隔离运行指定配置目录：

```bash
CAPTURE_TO_NOTION_CONFIG_DIR=/tmp/capture-to-notion capture-to-notion cache inspect
```

运行只读诊断：

```bash
capture-to-notion doctor
```

`doctor` 会检查配置路径、token 是否已配置、旧配置目录是否存在，并且不会打印 token 原文。

如需从旧目录迁移配置，可先预览：

```bash
capture-to-notion config migrate
```

确认后再执行迁移：

```bash
capture-to-notion config migrate --confirmed
```

迁移命令只复制 allowlist 中的配置资产，不覆盖新目录已有文件，也不删除旧目录。

## 常用命令

检查本地缓存：

```bash
capture-to-notion cache inspect
```

列出已缓存目标，不调用 Notion：

```bash
capture-to-notion target list
```

查看目标缓存详情：

```bash
capture-to-notion target inspect --alias books --compact
```

搜索 Notion 目标：

```bash
capture-to-notion target search --query "书单" --limit 5 --compact
```

扫描已确认目标：

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

绑定写入 profile，并显式声明可信的 Notion `files` 上传字段：

```bash
capture-to-notion target bind-profile \
  --alias books \
  --graph-id GRAPH_ID \
  --profile-id PROFILE_ID \
  --content-type book \
  --data-source-id DATA_SOURCE_ID \
  --field title=Name \
  --asset-field cover=Cover
```

执行写入前预检：

```bash
capture-to-notion capture preflight --input input.json --compact
```

生成写入计划：

```bash
capture-to-notion capture plan --input input.json --output plan.json --compact
```

用户确认后执行写入：

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

只读验证已写入页面：

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

## 标准工作流

1. 根据用户内容构造 `input.json`。
2. 先运行 `capture preflight --compact`。
3. 严格按 `workflow.planning.next_action` 路由下一步：推荐目标、选择目标、扫描目标、同步缓存、确认风险或生成计划。
4. 只有当 preflight 允许 `capture_plan` 时，才运行 `capture plan --compact`。
5. 审查计划中的目标路径、具体写入页面、字段映射、relation / people 需求、文件资产动作、警告和验证预期。
6. 用户明确确认后，才运行 `capture apply --confirmed`。
7. 写入后按计划预期验证结果，不只凭外链或字段存在声称成功。

## 文件资产上传

Notion `files` 字段应作为资产上传目标处理。对于封面、作者图片等字段，计划中应能看到：

- `asset_actions` 包含 `download_and_attach`
- 完整 plan 中包含 `asset_operations`
- apply 结果中返回 Notion 上传实体，例如 `file_upload`

如果目标 profile 没有可信资产映射，planner 不会把普通 URL 自动当成 files 上传目标。可以通过 `target bind-profile --asset-field semantic=NotionFilesProperty` 明确绑定。

## Parser Profile 与字段来源

目标缓存可以定义 `parser_profile`。它用于控制输入解析、必需字段、摘要字段、可信字段来源和资产上传信任要求。

`field_sources` 记录字段映射来自哪里。对于需要可信映射的字段，只有来源出现在 `trusted_field_sources` 中才会被直接信任，否则计划会产生确认或阻断类 warning。

常见字段来源包括：

- `explicit`：显式配置或用户确认的映射。
- `profile`：写入 profile 中定义的可信映射。
- `user_binding`：普通绑定，可能不足以满足某些资产或必需字段的信任规则。

## 安全边界

- 不静默写入 Notion。
- 不在 Capture to Notion 流程中回退使用 Notion MCP。
- 不直接调用底层 Notion API 或临时脚本绕过 CLI。
- Notion token、缓存和本地配置不应提交到仓库。
- 首次使用目标或目标结构变化后，应先扫描/同步，再生成写入计划。
- 计划里有未解决的 relation、people、资产或目标风险时，应先让用户选择、补充或确认。

## 测试

运行完整测试：

```bash
uv run pytest -q
```

如果只修改 README、文档或说明文字，通常只需要检查 diff：

```bash
git diff --check
```

## 发布状态

`0.1.0` 当前是内部 Beta，用于个人和内部日常使用。安装、验证、回滚和版本策略见 [RELEASE.md](RELEASE.md)。
