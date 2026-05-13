# Capture to Notion 当前完成度与下一阶段开发计划

## 文档定位

`PLAN.zh-CN.md` 继续作为 Capture to Notion 的 11 个改进方向源头基准。本文档只记录当前完成度判断、剩余开发任务和下一阶段执行顺序。

后续开发优先按本文档推进；当实现方向发生变化时，同步更新本文档，避免旧计划继续驱动已否定或已完成的实现方向。

## 总体状态

截至当前，11 个方向不是全部完成，而是基础能力已基本成型，剩余工作集中在迁移治理、真实验证、易用性、token 成本和复用规范。

当前状态：

- 已完成：1 项
- 基本完成：1 项
- 大部分完成：3 项
- 部分完成：5 项
- 未系统完成：1 项

## 11 个方向完成度

| # | 方向 | 优先级 | 当前状态 | 后续是否继续开发 | 当前说明 |
|---|---|---|---|---|---|
| 1 | 安全与副作用控制 | P0 | 基本完成 | 是 | plan-first、apply confirmation、token 不泄露、不回退 Notion MCP、doctor/version 不初始化 adapter 等已覆盖；仍需要真实 Notion apply 前后的端到端验证补强。 |
| 2 | 代码兼容性 | P0 | 已完成 | 回归维护 | `capture-to-notion` CLI、`capture_to_notion` 包导入、配置路径、环境变量、README 命令一致性已有测试覆盖。 |
| 3 | 版本与迁移治理 | P0 | 部分完成 | 是 | rename、旧名残留扫描、CHANGELOG、legacy config warning 已完成；`capture-to-notion config migrate` 仍未实现。 |
| 4 | 可观测性与调试能力 | P0/P1 | 部分完成 | 是 | `doctor`、`version` 已完成；doctor 已能提示 token、旧配置、stale target cache；`logs inspect` 仍未实现，是否需要先评估。 |
| 5 | 评估与回归测试 | P1 | 大部分完成 | 是 | CLI、planner、golden、writer、capture apply、schema、scanner、verifier 等测试网已建立；仍需逐项补齐 PLAN 中列出的 golden 场景。 |
| 6 | 快速完成用户要求 | P1 | 大部分完成 | 是 | `target list`、`target inspect`、cache 复用、plan 摘要已完成；仍可继续减少重复扫描、优化确认轮次和摘要体验。 |
| 7 | 文字正确性与输出质量 | P1 | 大部分完成 | 是 | 状态映射、content type、关键字段、summary、parser profile 驱动解析已完成；更多中英文标题、作者、ISBN 解析 golden case 仍可补。 |
| 8 | 页面与视觉正确性 | P1/P2 | 部分完成 | 是 | `capture verify --page-id`、apply 后 verification summary、files/page cover URL 检查方向已完成；真实 Notion 页面 cover、书籍封面、files 字段可显示性还需要真实环境端到端验证。 |
| 9 | 易用性 | P2 | 部分完成 | 是 | README/README.zh-CN、常用命令、doctor、target list/inspect 已有；中文错误信息、FAQ、下一步命令提示仍可继续优化。 |
| 10 | Token 成本与触发精准度 | P2 | 未系统完成 | 是 | `SKILL.md` 瘦身、description 精准化、重型资料外移还没有作为独立任务系统执行。 |
| 11 | 通用性与复用能力 | P3 | 部分完成 | 是 | planner/schema/verifier/scanner 边界已明显通用化；仍需总结规范，不提前抽公共框架。 |

## 已完成能力清单

当前已经落地的主要能力：

- `capture-to-notion` CLI / package rename。
- `capture-to-notion version`。
- `capture-to-notion doctor`。
- secret redaction。
- target cache。
- `capture-to-notion target list`。
- `capture-to-notion target inspect`。
- plan summary。
- required key fields。
- parser profile。
- trusted field sources。
- asset trust。
- explicit warning policy。
- scanner official property type boundary。
- stale target cache warning。
- `capture-to-notion capture verify --page-id`。
- apply 后 verification summary。
- Notion official property type regression net。
- no Notion MCP fallback 约束。

## 不再作为后续实现方式的方向

以下方向已经被当前设计否定，后续不应重新引入：

- 不在 schema、planner、verifier 中根据 Notion 字段名猜业务语义。
- 不在 Capture to Notion 流程中回退使用 Notion MCP。
- 不把 book、cover、author、ISBN、page_count 等业务字段硬编码进通用层。
- 不为了单个业务库提前抽公共框架。
- 不让写入绕过 plan-first 和 confirmation 流程。

## 下一阶段开发计划

### Task A：同步状态文档

优先级：P0  
对应方向：全部

目标：

- 新建当前状态文档。
- 保留 `PLAN.zh-CN.md` 作为 11 个方向源头。
- 后续开发以本文档的当前状态和下一阶段计划为准。

验收标准：

- 本文档存在并准确记录 11 个方向完成度。
- 不修改 `PLAN.zh-CN.md` 的源头基准定位。
- 旧计划文件仍作为历史执行记录保留。

### Task B：doctor / rescan 可观测性增强

优先级：P1  
对应方向：4. 可观测性与调试能力、9. 易用性

目标：

让 stale cache、缺 token、目标未扫描、schema 过期等常见问题给出更明确的下一步命令。

范围：

- doctor stale target cache warning 输出具体 target 名称。
- 能推导时给出建议 rescan 命令。
- 常见失败补充下一步操作。
- 评估是否需要 `capture-to-notion logs inspect`。
- 如果实现 `logs inspect`，必须只读、不泄露 token。

测试建议：

- stale `field_sources` cache。
- partial `field_sources` cache。
- 缺 token。
- 旧配置目录存在。
- 输出不包含 token。

### Task C：补齐 PLAN golden cases

优先级：P1  
对应方向：5. 评估与回归测试、7. 文字正确性与输出质量、8. 页面与视觉正确性

目标：

对照 `PLAN.zh-CN.md` 的 golden case 列表，补齐尚未系统覆盖的场景。

范围：

- 封面下载失败。
- 图片或文件 URL 不可访问。
- 目标未扫描。
- schema 过期。
- 多作者。
- 同名目标。
- 中英文标题、作者、ISBN 解析。

测试建议：

- 继续使用本地 cache fixture，不真实写入 Notion。
- 每个新增行为先写失败测试。
- 保持业务字段来源来自 parser profile、target cache、write plan 或显式 mapping。

### Task D：真实 Notion 端到端验证

优先级：P1  
对应方向：1. 安全与副作用控制、8. 页面与视觉正确性

目标：

验证当前 Skill 是否能在真实 Notion 环境中完成 scan、plan、apply、verify，并确认不需要 Notion MCP fallback。

范围：

- 真实 target scan。
- capture plan。
- capture apply。
- capture verify。
- 验证 page cover、files 字段、关键字段实际可见。
- 主写入成功和素材失败分开说明。

验收标准：

- 真实 Notion 页面可创建或更新。
- page cover 可访问性被验证。
- 计划声明的 files 字段可访问性被验证。
- verification warning 不掩盖 apply 结果。
- 全程不使用 Notion MCP。

注意：

- 这是带外部副作用的验证任务，执行前必须再次确认目标页面、目标数据库和写入内容。

### Task E：版本与迁移治理补齐

优先级：P1  
对应方向：3. 版本与迁移治理

目标：

设计并实现安全的配置迁移能力。

范围：

- 设计 `capture-to-notion config migrate`。
- 默认 dry-run。
- `--confirmed` 才实际迁移。
- 不覆盖新配置。
- 不打印 token。
- 不自动删除旧配置。

测试建议：

- 旧配置存在，新配置不存在。
- 旧配置和新配置同时存在。
- token 不出现在 stdout/stderr。
- dry-run 不写文件。
- 未确认时不迁移。

### Task F：易用性与 token 成本优化

优先级：P2  
对应方向：9. 易用性、10. Token 成本与触发精准度

目标：

减少日常使用认知负担和 Skill 加载成本。

范围：

- README 常用命令前置。
- README.zh-CN 改成日常使用导向。
- FAQ 增加常见问题：
  - 缺 token。
  - 目标未扫描。
  - alias 不存在。
  - schema 过期。
  - 图片不可访问。
  - 需要确认。
- 中文错误提示补下一步命令。
- 精简 `SKILL.md`。
- description 只保留触发条件。
- 安装、完整命令、测试、维护说明移到 README/docs。

测试建议：

- 文档命令与 CLI 保持一致。
- `SKILL.md` 精简后仍保留 plan-first、confirmation、no MCP fallback、target scan/plan/apply 主路径。

### Task G：复用规范沉淀

优先级：P3  
对应方向：11. 通用性与复用能力

目标：

总结 Capture to Notion 已验证的可复用模式，但不提前抽公共框架。

范围：

- plan / apply 模式。
- doctor 模式。
- verify 模式。
- target scan / list / inspect 模式。
- parser profile 模式。
- trusted field sources 模式。
- asset trust / fallback 模式。
- README / SKILL.md 分层模式。

验收标准：

- 只写规范，不抽公共库。
- 至少等第二个 Skill 出现同类需求后再考虑复用库。
- 不影响 `capture-to-notion` 自身稳定性。

## 推荐执行顺序

1. Task A：同步状态文档。
2. Task B：doctor / rescan 可观测性增强。
3. Task C：补齐 PLAN golden cases。
4. Task D：真实 Notion 端到端验证。
5. Task E：版本与迁移治理补齐。
6. Task F：易用性与 token 成本优化。
7. Task G：复用规范沉淀。

这个顺序优先建立当前开发基准，然后补诊断和测试，再做真实外部验证；迁移能力带文件副作用，放在诊断和测试更稳之后；Skill 瘦身和复用规范最后做，避免能力仍在变化时反复调整文档。

## 当前执行原则

- 涉及代码行为变更时，先写失败测试，再实现。
- 涉及 Notion 写入、迁移、删除、覆盖等外部副作用时，先确认目标和影响范围。
- 具体字段要求来自 target scan 缓存、Notion API 返回结构、写入计划、parser profile 或显式 mapping。
- 通用代码只按 Notion 官方 property object / page property value 类型处理。
- 不回退 Notion MCP；如果 Skill 缺能力，优先修 Skill 自己的 API 封装和流程。
