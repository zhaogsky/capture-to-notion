# Capture to Notion

[English version](README.en.md)

Capture to Notion 是一个 Claude Code Skill，用来把书籍、播客、文章、笔记等内容安全写入 Notion。它自带本地 CLI 后端，但默认使用方式应该是：你在 Claude Code 里提出保存/补全/写入 Notion 的需求，Claude 按 `SKILL.md` 的流程先预检、再生成计划、最后在你确认后写入。

核心原则：**先生成可审查的 preflight / plan，再由用户确认后 apply**。它不会静默写入 Notion，也不会在这个流程里回退使用 Notion MCP。

## 适合做什么

- 把书籍、播客单集、文章、笔记等内容写入指定 Notion 目标。
- 初始化或补全已有条目，例如补封面、作者、ISBN、页数、作者图片等。
- 根据已扫描的 Notion 结构生成写入计划。
- 解析 relation / people 字段，阻断未解决或有歧义的写入。
- 把 Notion `files` 字段作为上传资产处理，而不是只保存图片外链。
- 写入后按计划预期验证页面、字段、文件、封面和可验证的视图约束。

## 安装 Skill

把仓库放到 Claude Code 的用户 Skill 目录：

```bash
git clone https://github.com/zhaogsky/capture-to-notion.git ~/.claude/skills/capture-to-notion
```

如果 Claude Code 当前会话没有自动识别新 Skill，重启会话后再使用。

项目目录结构：

- `SKILL.md`：Claude Code 读取的 Skill 工作流、安全边界和确认规则。
- `capture_to_notion/`：Skill 使用的本地 CLI 后端。
- `tests/`：扫描、规划、写入、资产、验证和 CLI 行为的回归测试。
- `README.en.md`：英文说明。

## 配置 Notion API Key

主要配置是 Notion integration token。默认配置目录是：

```text
~/.config/capture-to-notion/
```

推荐使用环境变量保存 token：

```bash
export NOTION_TOKEN="secret_xxx"
```

也可以在本地配置文件中指定 token 或 token 环境变量名：

```json
{
  "notion": {
    "auth": {
      "env_token_name": "NOTION_TOKEN"
    },
    "api_version": "2026-03-11"
  }
}
```

如果确实要把 token 写入配置文件，应只写入本机的 `~/.config/capture-to-notion/config.json`，不要提交到仓库：

```json
{
  "notion": {
    "auth": {
      "token": "secret_xxx"
    },
    "api_version": "2026-03-11"
  }
}
```

检查配置是否可用：

```bash
capture-to-notion doctor
```

`doctor` 只检查 token 是否配置，不会打印 token 原文。

## 在 Claude Code 中怎么用

安装并配置后，可以直接用自然语言让 Claude 执行 Capture to Notion 工作流，例如：

```text
把《可能性的艺术》初始化到我的书单。
```

```text
把这期播客总结后保存到半拿铁播客库，先给我写入计划。
```

```text
给《世界作为参考答案》补充封面，要求上传到 Notion files 字段，不要只写图片 URL。
```

Skill 的正常流程是：

1. Claude 解析你的意图、内容类型、目标和输入形态。
2. 生成临时 `input.json`。
3. 先运行 `capture preflight --compact`。
4. 严格按 `workflow.planning.next_action` 决定下一步：推荐目标、选择目标、扫描目标、同步缓存、确认风险或生成计划。
5. 当 preflight 允许后，运行 `capture plan --compact`。
6. 向你展示目标路径、具体写入页面、字段映射、relation / people 需求、文件资产动作、警告和验证预期。
7. 只有你明确确认后，才运行 `capture apply --confirmed`。
8. 写入后按计划预期验证结果。

## CLI 后端命令

这些命令主要供 Skill 调用，也可用于调试和开发。

查看缓存：

```bash
capture-to-notion cache inspect
```

列出已缓存目标，不调用 Notion：

```bash
capture-to-notion target list
```

搜索 Notion 目标：

```bash
capture-to-notion target search --query "书单" --limit 5 --compact
```

扫描已确认目标：

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

查看目标详情：

```bash
capture-to-notion target inspect --alias books --compact
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

## CLI 安装方式

如果只是作为 Claude Code Skill 使用，重点是把仓库放到 `~/.claude/skills/capture-to-notion` 并配置 Notion token。

如果需要在 shell 里直接运行 `capture-to-notion` 命令，可以在项目目录中选择一种 Python 安装方式：

```bash
python -m pip install -e .
```

如果你使用 `uv`，也可以安装为可编辑工具：

```bash
uv tool install --force --editable .
```

验证命令：

```bash
capture-to-notion --help
```

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
python -m pytest -q
```

如果使用 `uv`：

```bash
uv run pytest -q
```

如果只修改 README、文档或说明文字，通常只需要检查 diff：

```bash
git diff --check
```

## 当前状态

截至 2026-06-03，当前支持的核心工作流已经进入回归维护状态。已有测试覆盖 cache-first 规划、compact 输出、目标路径展示、关系/人员字段安全、文件资产上传、apply 安全校验和写后验证等能力。

`0.1.0` 当前是内部 Beta，用于个人和内部日常使用。安装、验证、回滚和版本策略见 [RELEASE.md](RELEASE.md)。
