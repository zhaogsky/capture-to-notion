# Capture to Notion 真实场景 E2E 测试计划

## 目标

本计划用于反复验证 Capture to Notion 的真实链路。测试必须调用真实 Skill 后端 CLI、真实 Notion token、真实 cache-v2、真实 Notion 页面/数据库/data source。mock、fake adapter、虚拟 graph 只能作为单元回归，不能替代本计划。

重点验证：cache-first、可读路径展示、普通页面/结构化笔记/URL/播客/书籍/已有页面更新/relation/asset/views/view clone/cache miss-sync 的真实行为，以及所有写入都只落在 `ctn-e2e-*` 沙盒 alias 下。

## 执行原则

- 不使用 Notion MCP。
- 不使用 legacy cache fallback。
- 不直接写正式知识库目标；写入型场景只允许 `ctn-e2e-*` alias。
- 每轮标题和正文必须包含 `RUN_ID`。
- 真实写入只在 `--write-sandbox` 下执行；默认 `--readonly` 只做环境、preflight、plan、scan 等非业务写入检查。
- 清理/删除真实 Notion 对象不包含在脚本中，必须人工或另行授权执行。
- 脚本自动汇总 apply 输出中的 Notion 对象引用到 `cleanup/created-objects.md`。

## 沙盒目标

`init-sandbox.py` 会在父页面下准备/复用以下 `ctn-e2e-*` 目标：

```text
CTN E2E Sandbox
├── Plain Pages                  alias: ctn-e2e-plain-pages
├── Knowledge Notes              alias: ctn-e2e-knowledge-notes
├── URL Captures                 alias: ctn-e2e-url
├── Books                        alias: ctn-e2e-books
├── Authors                      alias: ctn-e2e-authors
├── Podcasts                     alias: ctn-e2e-podcasts
├── Guests                       alias: ctn-e2e-guests
├── View Clone Source            alias: ctn-e2e-view-source
│   └── View Source Items        alias: ctn-e2e-view-source-db
└── View Clone Target            alias: ctn-e2e-view-target
    └── View Target Items        alias: ctn-e2e-view-target-db
```

Books/Podcasts 会绑定 write profile；Authors/Guests 用作 relation completion 目标；View Source/Target 用于 views scan 与 view clone/remap。

初始化命令：

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion \
  python /Users/aaron/.claude/skills/capture-to-notion/scripts/e2e/init-sandbox.py
```

如果没有可用父页面，先设置：

```bash
CTN_E2E_SANDBOX_PARENT_PAGE_ID="..."
```

## 自动化场景矩阵

`run-real-e2e.sh --scenario all` 会枚举并执行/报告 E2E-01 到 E2E-15：

| 编号 | 场景 | 默认目标 | 写入策略 | 验收重点 |
|---|---|---|---|---|
| E2E-01 | 环境与 cache 盘点 | 全局 config/cache | 否 | doctor、cache inspect、target list |
| E2E-02 | target suggest | write_profile alias | 否 | suggestion 可用 |
| E2E-03 | target search | 真实 Notion 搜索 | 否 | compact path/parent_path |
| E2E-04 | plain text → child page | `ctn-e2e-plain-pages` | sandbox only | 普通子页面，不强制 data_source |
| E2E-05 | structured note → child page | `ctn-e2e-knowledge-notes` | sandbox only | 标题/正文 blocks/路径展示 |
| E2E-06 | podcast episode → data source | `ctn-e2e-podcasts` | sandbox only | profile mapping、字段、write_targets |
| E2E-07 | book initialized | `ctn-e2e-books` | sandbox only | writable_fields 包含 title/state/author/isbn/page_count/cover |
| E2E-08 | book completed/update | `ctn-e2e-books` | sandbox only | completed/update 路径，不重复创建 |
| E2E-09 | external URL gate | `ctn-e2e-url` | sandbox only | 不自动抓取 URL，不冒充全文总结 |
| E2E-10 | existing page update | `ctn-e2e-plain-pages` | sandbox only | 第二次写入进入 update 路径 |
| E2E-11 | relation completion | books/authors | sandbox only | relation 目标来自真实 Authors data source/page |
| E2E-12 | asset handling | books cover | sandbox only | cover asset 计划和 apply 输出 |
| E2E-13 | views scan | `ctn-e2e-view-source` | 否 | graph 缓存真实 views，`view_context=true` |
| E2E-14 | view clone/remap | source → target | sandbox only | 从 source graph 导出 views，create-database 时 remap |
| E2E-15 | cache miss/sync | `ctn-e2e-cache-miss` | 可选 sandbox | 缺 cache preflight、scan/sync 后再 plan/apply |

## 推荐执行顺序

```bash
# 1. 准备沙盒。会写入/复用 ctn-e2e-* 沙盒对象。
uv run --project /Users/aaron/.claude/skills/capture-to-notion \
  python /Users/aaron/.claude/skills/capture-to-notion/scripts/e2e/init-sandbox.py

# 2. 默认只读枚举全场景；不执行 capture apply。
/Users/aaron/.claude/skills/capture-to-notion/scripts/e2e/run-real-e2e.sh --readonly --scenario all

# 3. 真实沙盒写入全场景。
/Users/aaron/.claude/skills/capture-to-notion/scripts/e2e/run-real-e2e.sh --write-sandbox --scenario all
```

也可以单独执行：

```bash
scripts/e2e/run-real-e2e.sh --write-sandbox --scenario book-initialized
scripts/e2e/run-real-e2e.sh --write-sandbox --scenario relation
scripts/e2e/run-real-e2e.sh --scenario views-scan
scripts/e2e/run-real-e2e.sh --write-sandbox --scenario view-clone
```

## 跳过规则

允许 `SKIPPED`，但必须在 `RESULT.md` 写明原因：

- `--readonly` 下所有 apply / view clone 写入自动跳过。
- 写入目标 alias 不是 `ctn-e2e-*` 时，apply 自动跳过，避免误写正式库。
- E2E-13 如果 `CTN_E2E_VIEW_SOURCE_PAGE_ID` 未设置且 `ctn-e2e-view-source` alias 未缓存，则跳过。
- E2E-14 如果 source/target page id 无法从环境变量或 alias cache 解析，则跳过。
- E2E-15 如果未设置 `CTN_E2E_CACHE_MISS_PAGE_ID`，只记录 cache-miss preflight，跳过 scan/sync/apply。
- relation/asset/view clone 若运行时代码尚未支持对应能力，场景应 FAIL 或 SKIPPED，并保留 stdout/stderr 作为阻塞证据。

## 产物目录

每轮测试使用：

```bash
RUN_ID="CTN-E2E-$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="scripts/e2e/artifacts/$RUN_ID"
```

可用 `CTN_E2E_OUTPUT_DIR` 覆盖产物根目录，或用 `ARTIFACT_DIR` 指定单次运行的完整产物目录。

核心产物：

```text
scripts/e2e/artifacts/<RUN_ID>/
├── 00-env/
├── 01-suggest/
├── 01-search/
├── 02-plain-page/
├── 03-structured-note/
├── 04-podcast/
├── 05-book-initialized/
├── 06-book-completed/
├── 07-url-gate/
├── 08-existing-update/
├── 09-relation/
├── 10-assets/
├── 11-views-scan/
├── 12-view-clone/
├── 13-cache-miss-sync/
├── cleanup/created-objects.md
└── RESULT.md
```

## 标准流程

每个 capture 场景执行：

1. `capture preflight --compact`
2. 如果 next_action 是 `capture_plan` 或 `sync_target_cache`，执行 `capture plan --compact --output plan.json`
3. `--write-sandbox` 且目标 alias 为 `ctn-e2e-*` 时执行 `capture apply --confirmed`
4. 汇总 apply 输出到 `cleanup/created-objects.md`

场景专用流程：

- E2E-13 使用 `target scan --page-id/--alias`，并校验 scan 输出包含 views 且 `target_capabilities.view_context=true`。
- E2E-14 先扫描 source graph，再从 graph 导出 views 文件，最后在 target page 下 `target create-database --views`。
- E2E-15 先对缺 cache alias 做 preflight，再在提供 `CTN_E2E_CACHE_MISS_PAGE_ID` 时 scan/sync 并继续 plan/apply。

## 通过标准

必须满足：

- E2E-01 到 E2E-15 均被枚举并在 `RESULT.md` 中报告 PASS/FAIL/SKIPPED。
- 不使用 Notion MCP。
- 不 fallback legacy cache。
- 写入只发生在 `ctn-e2e-*` 沙盒 alias。
- plan 中有具体 `write_targets`、`target_path` / `target_path_complete`。
- 书籍场景的 `writable_fields` 明确列出 title/state/author/isbn/page_count/cover。
- views scan 真实缓存 views 并做 graph validation。
- 脚本不执行删除/清理动作。
