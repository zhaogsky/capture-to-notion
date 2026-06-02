# Capture to Notion

[English version](README.en.md)

Capture to Notion 是一个 Claude Code Skill，用来把书籍、文章、播客、笔记等内容保存到 Notion。

你只需要在 Claude Code 里用自然语言说明想保存什么、保存到哪里。这个 Skill 会先检查配置和目标，生成一份可审查的写入计划；只有在你确认之后，它才会真正写入 Notion。

## 它能做什么

- 把书籍、文章、播客、笔记等内容保存到 Notion。
- 写入前生成计划，让你先确认目标、字段和内容。
- 只在用户明确确认后写入，不会静默修改 Notion。
- 支持把封面、头像、配图上传到 Notion `files` 字段。
- 支持 relation、people 等字段的解析和安全检查。
- 写入后按计划验证结果。

## 安装

### 1. 下载 Skill

把仓库下载到 Claude Code 的 Skill 目录：

```bash
git clone https://github.com/zhaogsky/capture-to-notion.git ~/.claude/skills/capture-to-notion
```

如果当前 Claude Code 会话没有识别到新 Skill，重启会话即可。

### 2. 安装本地 CLI 后端

Skill 会调用本地命令 `capture-to-notion` 来完成 Notion 的扫描、规划和写入。

进入项目目录：

```bash
cd ~/.claude/skills/capture-to-notion
```

安装命令：

```bash
python -m pip install -e .
```

如果你使用 `uv`，也可以这样安装：

```bash
uv tool install --force --editable .
```

确认命令可用：

```bash
capture-to-notion --help
```

## 配置 Notion API Key

### 1. 创建 Notion integration

先在 Notion 创建一个 integration，并复制它的 API key。它通常长这样：

```text
secret_xxx
```

还需要把这个 integration 邀请到你要写入的 Notion 页面或数据库中。否则，即使 API key 配好了，Notion API 也没有权限访问目标页面。

### 2. 写入本地配置文件

推荐把 API key 写到本机配置文件里。

当前默认配置文件位置是：

```text
~/.config/capture-to-notion/config.json
```

在 Windows 上，当前实现对应的默认路径是：

```text
C:\Users\<你的用户名>\.config\capture-to-notion\config.json
```

创建配置目录：

```bash
mkdir -p ~/.config/capture-to-notion
```

编辑配置文件：

```bash
nano ~/.config/capture-to-notion/config.json
```

填入下面的内容：

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

把 `secret_xxx` 换成你自己的 Notion API key。

这个文件只应该保存在你的电脑本地，不要提交到 GitHub，也不要分享给别人。

### 3. 检查配置

运行：

```bash
capture-to-notion doctor
```

`doctor` 会检查配置目录、运行路径和 Notion token 是否已配置；它不会打印 token 原文。

注意：Skill 不会在安装时自动完成所有环境检查。第一次使用前，建议手动运行一次 `doctor`。实际写入前，Skill 还会通过 preflight 检查目标、缓存和写入计划。

## 在 Claude Code 里怎么用

安装和配置完成后，可以直接在 Claude Code 里这样说：

```text
把这本书保存到我的 Notion 书单，状态设为想读。
```

```text
把这篇文章总结后保存到我的 Notion 阅读数据库，先给我写入计划。
```

```text
把这个播客单集保存到 Notion，补全标题、节目名、链接和摘要。
```

```text
给这个作者条目补充头像，上传到 Notion files 字段，不要只保存图片 URL。
```

```text
把这段会议记录保存到 Notion 的项目笔记页面下，先确认目标路径。
```

这些例子只是说明用法。实际使用时，你可以换成自己的 Notion 页面、数据库和字段。

## Skill 的工作流程

每次写入 Notion 时，Skill 会按下面的流程执行：

1. 理解你的请求：内容是什么、目标在哪里、是直接写入还是先补全信息。
2. 生成输入文件。
3. 运行 preflight，检查目标、缓存、配置和下一步动作。
4. 如果目标还没扫描过，会要求搜索、选择或扫描目标。
5. 生成写入计划。
6. 展示目标路径、字段映射、具体写入页面、文件上传动作和 warning。
7. 等你明确确认后，才执行写入。
8. 写入后按计划验证结果。

核心原则是：**先计划，再确认，最后写入**。

## 常用 CLI 命令

日常使用时，你主要和 Claude Code 对话，不需要手动运行很多命令。下面这些命令主要用于初次配置、调试或高级使用。

检查环境：

```bash
capture-to-notion doctor
```

搜索 Notion 目标：

```bash
capture-to-notion target search --query "书单" --limit 5 --compact
```

扫描目标：

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

写入前预检：

```bash
capture-to-notion capture preflight --input input.json --compact
```

生成写入计划：

```bash
capture-to-notion capture plan --input input.json --output plan.json --compact
```

确认后写入：

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

## 关于文件上传

如果 Notion 字段类型是 `files`，这个 Skill 会把图片当作文件资产处理。

例如封面、作者头像、配图等，计划中应该能看到：

- `download_and_attach`
- `asset_actions`
- `asset_operations`
- 写入结果中的 Notion 上传文件实体

这和“只把图片 URL 写进 Notion”不同。

## 安全说明

- 不会静默写入 Notion。
- 写入前会先生成计划。
- 只有用户明确确认后才会 apply。
- Notion API key 只保存在本地配置文件中。
- 不要提交 `~/.config/capture-to-notion/config.json`、缓存或 token。
- Capture to Notion 流程不回退使用 Notion MCP。

## 高级配置

普通用户建议直接使用上面的 `config.json` token 配置方式。

如果你是开发者，或者需要在 CI / 临时 shell 中使用，也可以改用环境变量。配置文件可以写成：

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

然后设置环境变量：

```bash
export NOTION_TOKEN="secret_xxx"
```

普通用户可以忽略这一节。

## 测试

开发时可以运行测试：

```bash
python -m pytest -q
```

如果使用 `uv`：

```bash
uv run pytest -q
```

只改 README 或文档时，通常检查格式即可：

```bash
git diff --check
```
