# Capture to Notion 改进开发计划

## 总目标

围绕 `capture-to-notion` Skill 的 11 个改进方向，按开发重要性排序推进。目标是先保证安全、兼容、迁移和可诊断，再增强写入正确性，最后优化体验、token 成本和长期复用。

## 11 个方向开发重要性排序

| 顺序 | 改进方向 | 优先级 | 说明 |
|---|---|---|---|
| 1 | 安全与副作用控制 | P0 | Notion 写入是外部副作用，必须先保证不误写、不误改、不泄密。 |
| 2 | 代码兼容性 | P0 | CLI、包名、配置路径必须稳定，否则后续功能都不可靠。 |
| 3 | 版本与迁移治理 | P0 | 已经历重命名，必须防止旧名、旧配置、旧命令残留。 |
| 4 | 可观测性与调试能力 | P0/P1 | 出错时要能快速定位，doctor、version、友好错误是基础。 |
| 5 | 评估与回归测试 | P1 | 后续每次改 planner、writer、verify 都需要测试兜底。 |
| 6 | 快速完成用户要求 | P1 | 在安全基础上减少确认轮次、重复搜索和重复扫描。 |
| 7 | 文字正确性与输出质量 | P1 | Notion 条目的标题、摘要、状态、字段映射要稳定准确。 |
| 8 | 页面与视觉正确性 | P1/P2 | Notion 不做复杂视觉回归，但要做字段和图片可显示性验证。 |
| 9 | 易用性 | P2 | README、错误提示、常用命令、target list 等提升日常体验。 |
| 10 | Token 成本与触发精准度 | P2 | 流程稳定后再瘦身 Skill，避免频繁返工。 |
| 11 | 通用性与复用能力 | P3 | 最后做，避免过早抽象；先把 capture-to-notion 自己做好。 |

---

# Phase 1：安全与基础稳定

对应方向：

1. 安全与副作用控制
2. 代码兼容性
3. 版本与迁移治理

## 1. 安全与副作用控制

### 目标

确保 `capture-to-notion` 不会误写 Notion、误改 schema、泄露 token，或绕过确认流程。

### 要做

- 保持所有 Notion 写入都是 plan-first。
- `capture apply` 必须要求明确确认。
- 确认不回退 Notion MCP。
- token 只从 Skill 自有配置读取。
- 日志、错误、debug 输出都不能打印 token。
- 配置迁移、覆盖、删除等操作必须显式确认。
- 写入计划必须展示：
  - 目标页面
  - 目标数据库
  - 条目标题
  - 状态
  - 关键字段
  - 封面处理方式
  - 是否需要确认

### 验收标准

- 没有任何命令会静默写入 Notion。
- 没有任何路径会自动修改 Notion schema。
- apply 没有 `--confirmed` 时不会执行写入。
- token 不出现在 stdout、stderr、日志、测试快照中。

## 2. 代码兼容性

### 目标

保证 CLI、Python 包、配置路径、安装方式在当前机器和后续环境中稳定。

### 要做

- 测试真实 CLI 入口：`capture-to-notion --help`。
- 测试 Python 包导入：`python -c "import capture_to_notion"`。
- 确认 `pyproject.toml` 中：
  - package name 是 `capture-to-notion`
  - console script 是 `capture-to-notion`
  - import path 是 `capture_to_notion`
- 配置路径统一为 `~/.config/capture-to-notion/`。
- 环境变量统一为 `CAPTURE_TO_NOTION_CONFIG_DIR`。
- 测试里使用临时目录，不依赖真实 `/Users/aaron` 配置。

### 验收标准

- `capture-to-notion --help` 可运行。
- `capture_to_notion` 可导入。
- 测试不依赖真实本机配置。
- README 中安装命令和实际 CLI 一致。

## 3. 版本与迁移治理

### 目标

防止重命名留下长期混乱。

### 要做

- 增加旧名残留扫描。
- 新增 `CHANGELOG.md`。
- 记录迁移历史：
  - `notion`
  - `notion-skill`
  - `notion-capture`
  - `notion_skill`
  - `~/.config/notion-skill`
- 已实现安全的 `capture-to-notion config migrate`。
- 配置迁移默认 dry-run。
- `--confirmed` 后才迁移。
- 不打印 token。
- 不覆盖新配置。

### 验收标准

- 旧名只允许出现在 changelog、迁移说明、测试 fixture 中。
- 代码、命令、配置、README 不再混用旧名。
- 后续重命名时有明确检查流程。

---

# Phase 2：可诊断、可测试、可恢复

对应方向：

4. 可观测性与调试能力
5. 评估与回归测试

## 4. 可观测性与调试能力

### 目标

让 Claude 和用户都能快速判断环境、配置、缓存、目标是否正常。

### 要做

优先新增：

```bash
capture-to-notion doctor
```

检查：

- CLI 是否可用。
- Python 包是否可导入。
- 配置目录是否存在。
- token 是否配置。
- cache / targets / plans / logs 是否可写。
- 是否存在旧配置目录。
- 是否存在旧命令残留。
- 当前默认 workspace 是否可读。

再新增：

```bash
capture-to-notion version
```

输出：

- CLI version。
- Python package path。
- config root。
- Skill path。
- 是否 editable install。
- 当前命令名。

后续可加：

```bash
capture-to-notion logs inspect
```

### 验收标准

- `doctor` 不写 Notion。
- `doctor` 不打印 token。
- 失败时给出下一步建议。
- `version` 能帮助定位 editable install 指向错误。

## 5. 评估与回归测试

### 目标

后续改 planner、writer、asset、relation、verify 时不会破坏已有能力。

### 要做

建立黄金用例：

- 初始化书籍。
- 完成书籍。
- 缺 ISBN。
- 多作者。
- 播客单集。
- 同名目标。
- 封面下载失败。
- 图片或文件 URL 不可访问。
- 目标未扫描。
- schema 过期。

测试类型：

- CLI 入口测试。
- planner 输出测试。
- writer 写入 payload 测试。
- asset download fallback 测试。
- relation resolution 测试。
- 错误输出测试。
- 旧名残留扫描测试。

### 验收标准

- 核心流程修改后测试能发现回归。
- 重要行为都有 golden case。
- 测试不真实写入 Notion，除非明确做集成验证。

---

# Phase 3：正确写入与结果验证

对应方向：

6. 快速完成用户要求
7. 文字正确性与输出质量
8. 页面与视觉正确性

## 6. 快速完成用户要求

### 目标

用户一句话说“想读某书”或“我读完了某书”，Claude 能更快生成正确计划。

### 要做

- 优化 target cache / alias 使用。
- 增加：

```bash
capture-to-notion target list
capture-to-notion target inspect --alias books
```

- 减少重复 target search。
- schema 未变化时复用缓存。
- plan 摘要固定格式。
- 对低风险只读操作自动执行。
- 对写入仍保持确认。

### 验收标准

- 常用目标不需要每次重新搜索。
- 用户能快速知道有哪些 alias。
- 计划摘要短而完整。
- 不因为提速跳过确认。

## 7. 文字正确性与输出质量

### 目标

Notion 条目的标题、状态、字段、摘要、备注不跑偏。

### 要做

- 集中状态映射：
  - 初始化 / 想读 / 待读 / 待听 → `initialized`
  - 完成 / 已读 / 读完 / 听完 → `completed`
- 集中内容类型判断：
  - book
  - podcast_episode
  - article / null
- 优化 plan 中的字段展示。
- 对书籍条目确保不只写最小字段。
- 缺关键字段时给 warning，而不是静默忽略。
- 中英文书籍标题、作者、ISBN 解析规则进入测试。

### 验收标准

- 状态映射稳定。
- 内容类型判断稳定。
- 书籍条目计划中能看到封面、作者、ISBN、页数等关键字段。
- 不能获取完整元数据时提前说明。

## 8. 页面与视觉正确性

### 目标

Notion 不做复杂页面视觉回归，但必须验证字段和素材真的可显示。

### 要做

新增：

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

验证：

- 页面存在。
- 标题字段存在。
- 状态字段正确。
- 作者 relation 存在。
- ISBN 存在。
- 页数存在。
- 书籍封面字段有可访问图片。
- 页面 cover 可访问。
- target scan 缓存、计划或显式映射声明的 files 字段可访问。
- 不把特定业务字段作为通用 verifier 固定检查项。

apply 后展示验证摘要：

```text
写入完成：
- 页面：...
- 标题：已确认
- 状态：已确认
- 封面字段：可显示
- 页面 cover：可显示
- 计划声明的图片或文件字段：可显示 / 缺失 / 不可访问
- 警告：...
```

### 验收标准

- 不把 Notion 当成 HTML 页面做视觉测试。
- 但图片 URL 必须真实可访问。
- 页面 cover、书籍封面，以及 target scan 缓存、计划或显式映射声明的 files 字段分别验证。
- 主写入成功和素材失败要分开说明。

---

# Phase 4：日常使用体验优化

对应方向：

9. 易用性
10. Token 成本与触发精准度

## 9. 易用性

### 目标

用户不用记内部细节，也能知道怎么用、怎么修。

### 要做

- README 前置常用命令：
  - help
  - doctor
  - cache inspect
  - target search
  - target scan
  - capture plan
  - capture apply
  - capture verify
- README.zh-CN 面向日常使用。
- README.md 面向通用维护。
- 错误信息中文友好化。
- 常见错误给下一步命令。
- 补充常见问题：
  - 缺 token
  - 目标未扫描
  - alias 不存在
  - schema 过期
  - 图片不可访问
  - 需要确认

### 验收标准

- README 前半部分能解决 80% 使用问题。
- 出错时用户知道下一步。
- 中文 README 不只是英文直译。

## 10. Token 成本与触发精准度

### 目标

减少 Claude 使用 Skill 时的加载成本和误触发。

### 要做

- 精简 `SKILL.md`。
- description 只写触发条件，不写流程。
- 高频执行规则保留在 `SKILL.md`。
- 安装、测试、迁移、完整命令放 README。
- 复杂参考内容放 docs。
- 输出格式压缩成短模板。

`SKILL.md` 保留：

- 什么时候用。
- plan-first。
- apply-after-confirmation。
- 不回退 Notion MCP。
- target search / scan / plan / apply 主路径。
- 简短输出格式。
- 安全规则。

移出：

- 安装说明。
- 配置细节。
- 完整命令参考。
- 测试说明。
- 维护说明。
- 长解释。

### 验收标准

- `SKILL.md` 明显变短。
- Claude 仍能正确执行 capture flow。
- README 承接详细内容。
- description 不导致误触发。

---

# Phase 5：最后再做复用抽象

对应方向：

11. 通用性与复用能力

## 11. 通用性与复用能力

### 目标

从 `capture-to-notion` 中沉淀可复用模式，但不提前抽象。

### 要做

在 `capture-to-notion` 稳定后，总结已验证的可复用规范：

- plan / apply 模式。
- doctor / version 模式。
- verify / validation 模式。
- target search / scan / inspect 模式。
- parser profile 模式。
- trusted field sources 模式。
- asset trust / fallback 模式。
- README / SKILL.md 分层模式。

`config migrate` 的安全迁移经验已归入版本与迁移治理回归维护；`logs inspect` 暂不实现，只有真实日志排障需求出现后再评估。暂时不要急着抽公共框架。

### 已验证可复用规范

以下规范来自 `capture-to-notion` 当前已验证的实现，用于未来开发类似 Skill 时复用判断，不代表当前要抽公共库。

#### 1. Plan / Apply 模式

- 写入类 Skill 必须保持 plan-first、apply-after-confirmation。
- `plan` 输出应区分用户审阅摘要和机器可执行 payload。
- 用户审阅摘要应列出目标、标题、状态、关键字段、素材动作、warning 和是否需要确认。
- 机器可执行 plan 应保存完整输入、目标、字段映射、操作列表、素材操作和确认原因。
- `apply` 只能消费已确认 plan，不在 apply 阶段重新推断用户意图。
- 有外部副作用的写入、迁移、覆盖、删除都必须显式确认。

#### 2. Doctor / Version 模式

- `doctor` 只做本地只读诊断，不初始化外部 API adapter，不写远端系统。
- 诊断输出应检查配置路径、token 是否配置、旧配置、缓存健康、stale/partial cache 和下一步命令。
- 诊断不能打印 token 原文；错误和 warning 也必须保持脱敏。
- `version` 应输出 CLI、包路径、运行路径、配置根目录等定位 editable install 和路径错配所需的信息。

#### 3. Verify / Validation 模式

- `verify` 应按计划、缓存或显式 mapping 验证结果，不根据业务字段名猜测语义。
- 验证应区分主写入结果和素材/图片可访问性结果，素材失败不能掩盖主写入结果。
- apply 后可附加 verification summary，但 verification warning 不应隐藏 apply 原始结果。
- 无 plan 或 mapping 时，verify 只能做通用存在性和可访问性检查。

#### 4. Target Search / Scan / Inspect 模式

- target search 只负责发现候选，不在候选不唯一时自动绑定目标。
- target scan 负责把外部结构转成 cache，cache 中记录官方字段类型、data source、schema hash、字段映射来源等事实。
- target inspect 默认可输出完整 cache；对话或 Skill 审阅应提供 compact 输出，避免展开大 schema。
- 写入流程应 cache-first：可信 cache 存在时直接计划；仅在 cache 缺失、用户要求重扫，或远端返回结构过期错误后重扫。
- 同名目标、导航型页面、相邻数据库等歧义场景必须让用户选择精确目标。

#### 5. Parser Profile 模式

- 业务解析规则放在 parser profile、target cache 或显式输入中，不硬编码进通用 planner/schema/verifier。
- profile 可定义 labels、title patterns、required schema fields、required value fields、summary key fields、summary policy 和 asset trust required fields。
- data source 层 profile 可以覆盖 target 层 profile。
- 默认 profile 只能提供通用默认值和审阅要求，不能偷偷根据 Notion 字段名推断业务语义。

#### 6. Trusted Field Sources 模式

- 字段映射应记录 `field_sources`，区分 scan、profile、explicit 等来源。
- required mapping 只有来源在 active profile 的 `trusted_field_sources` 中时才可直接满足信任门槛。
- 不可信映射应转成 warning 或 confirmation，而不是静默写入。
- 缺 schema、缺 value、映射不可信应在 plan summary 中清楚呈现。

#### 7. Asset Trust / Fallback 模式

- 素材字段只有在映射来源可信时才规划上传或附件写入。
- 下载、缓存、上传、外链 fallback 应与主写入操作分开记录。
- 素材下载失败时主写入可以继续，但必须在 `asset_results` 和顶层 warning 中暴露。
- 页面 cover、files 字段、外链可访问性应分别验证，不能只凭字段存在就宣称素材成功。

#### 8. README / SKILL.md 分层模式

- `SKILL.md` 只保留 Claude 执行所需的高频流程、安全边界和输出模板。
- README 承接安装、配置、诊断、迁移、常用命令和测试说明。
- 中文 README 面向日常使用，英文 README 面向通用维护和跨环境说明。
- 对话场景优先使用 compact 输出；完整 JSON 保留给脚本、调试和 apply。
- 当实现方向变化时，同步更新执行计划、长期计划和文档，避免旧计划继续驱动已否定方向。

#### 9. 复用边界

- 当前阶段只沉淀规范，不抽公共库、不引入跨 Skill 框架。
- 至少等第二个类似 Skill 出现同类 plan/apply/doctor/verify/profile 需求后，再评估提取公共库。
- 抽象前应先比较两个 Skill 的真实差异，避免把 Notion 特有约束伪装成通用框架。

### 验收标准

- 至少等第二个 Skill 也有同类需求时再抽通用库。
- 当前阶段只写规范，不强行重构。
- 不影响 `capture-to-notion` 自身稳定性。

---

# 分批开发顺序

## 第一批：必须最先做

1. 安全与副作用控制。
2. 代码兼容性。
3. 版本与迁移治理。
4. 可观测性与调试能力。

落地任务：

- plan-first 检查。
- secret 脱敏。
- CLI 入口测试。
- 旧名残留扫描。
- `CHANGELOG.md`。
- `doctor`。
- `version`。

## 第二批：核心质量保障

5. 评估与回归测试。
6. 快速完成用户要求。
7. 文字正确性与输出质量。
8. 页面与视觉正确性。

落地任务：

- golden cases。
- target list / inspect。
- plan 摘要优化。
- 状态映射。
- 关键字段检查。
- capture verify。
- apply 后验证摘要。

## 第三批：体验与成本优化

9. 易用性。
10. Token 成本与触发精准度。

落地任务：

- README 常用命令置顶。
- 中文错误信息。
- README.zh-CN 优化。
- `SKILL.md` 瘦身。
- description 精准化。
- 重型资料移出主 Skill。

## 第四批：长期复用

11. 通用性与复用能力。

落地任务：

- 总结模式。
- 写规范。
- 等第二个 Skill 有同类需求再抽象。
- 不提前做大框架。

---

# 当前执行原则

1. 先保证不会误写、能运行、能迁移、能诊断。
2. 再保证写入内容正确、字段和图片可验证。
3. 然后优化日常使用体验和 token 成本。
4. 最后再考虑抽象复用到其他 Skill。
5. 每次涉及代码行为变更，先写失败测试，再实现。
