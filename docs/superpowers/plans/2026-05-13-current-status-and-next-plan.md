# Capture to Notion 当前完成度与下一阶段开发计划

## 文档定位

`PLAN.zh-CN.md` 继续作为 Capture to Notion 的 11 个改进方向源头基准。本文档只记录当前完成度判断、剩余开发任务和下一阶段执行顺序。

后续开发优先按本文档推进；当实现方向发生变化时，同步更新本文档，避免旧计划继续驱动已否定或已完成的实现方向。

## 总体状态

截至 2026-05-15，11 个方向不是全部完成，而是核心 capture 链路、cache-first 预检、profile-driven 计划、apply 验证、已有页面更新能力和 doctor/rescan 可观测性已基本成型。剩余工作集中在 golden case 补齐、素材/视觉真实验证、迁移治理、易用性/token 成本和复用规范。

最近基准提交：`4786556 feat: make capture planning cache-first and profile-driven`。

当前状态：

- 已完成：1 项
- 基本完成：1 项
- 大部分完成：5 项
- 部分完成：3 项
- 未系统完成：1 项

## 11 个方向完成度

| # | 方向 | 优先级 | 当前状态 | 后续是否继续开发 | 当前说明 |
|---|---|---|---|---|---|
| 1 | 安全与副作用控制 | P0 | 基本完成 | 是 | plan-first、apply confirmation、token 不泄露、不回退 Notion MCP、doctor/version 不初始化 adapter、possible partial write 防护、stale cache recovery、apply 后 verification 已覆盖；2026-05-15 已完成 existing_page_id 真实 update E2E。剩余：素材/files/page cover 类真实验证。 |
| 2 | 代码兼容性 | P0 | 已完成 | 回归维护 | `capture-to-notion` CLI、`capture_to_notion` 包导入、配置路径、环境变量、README 命令一致性已有测试覆盖。 |
| 3 | 版本与迁移治理 | P0 | 部分完成 | 是 | rename、旧名残留扫描、CHANGELOG、legacy config warning 已完成；`capture-to-notion config migrate` 仍未实现。 |
| 4 | 可观测性与调试能力 | P0/P1 | 大部分完成 | 是 | `doctor`、`version`、preflight 结构事实、apply verification summary 已完成；doctor 已能提示 token、旧配置、stale/partial target cache、具体 target 名称和可执行 rescan 命令；`logs inspect` 暂不实现，后续只在真实日志排障需求出现时再评估。 |
| 5 | 评估与回归测试 | P1 | 大部分完成 | 是 | CLI、planner、golden、writer、capture apply、schema、scanner、verifier、preflight、structure analyzer 等测试网已建立；2026-05-15 已补齐 schema 过期、单缺 ISBN、封面下载失败 apply 输出等关键回归，并确认目标未扫描、同名目标、URL 不可访问已有覆盖；后续只补真实样例长尾。 |
| 6 | 快速完成用户要求 | P1 | 大部分完成 | 是 | `target list`、`target inspect`、cache 复用、preflight、plan 摘要、existing_page_id update plan 已完成；仍可继续减少重复扫描、优化确认轮次和摘要体验。 |
| 7 | 文字正确性与输出质量 | P1 | 大部分完成 | 是 | 状态映射、content type、关键字段、summary-like field 阻塞、parser profile 驱动解析、states/config/profile-driven planning 已完成；2026-05-15 已补单缺 ISBN golden 覆盖；更多中英文标题、作者解析 golden case 仍可补。 |
| 8 | 页面与视觉正确性 | P1/P2 | 部分完成 | 是 | `capture verify --page-id`、apply 后 verification summary、files/page cover URL 检查方向已完成；2026-05-15 已验证已有页面 update 后关键字段存在，并补充封面下载失败 apply warning 回归。真实 Notion 页面 cover、书籍封面、files 字段可显示性仍需端到端验证。 |
| 9 | 易用性 | P2 | 部分完成 | 是 | README/README.zh-CN、常用命令、doctor、target list/inspect、preflight safe/blocked actions 已有；中文错误信息、FAQ、下一步命令提示仍可继续优化。 |
| 10 | Token 成本与触发精准度 | P2 | 未系统完成 | 是 | `SKILL.md` 已因 preflight/summary 变长；瘦身、description 精准化、重型资料外移还没有作为独立任务系统执行。 |
| 11 | 通用性与复用能力 | P3 | 大部分完成 | 是 | planner/schema/verifier/scanner 边界已明显通用化，状态、primary score、normalized record、summary policy、preflight/structure analyzer 均已配置/cache/profile 驱动；仍需总结规范，不提前抽公共框架。 |

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
- stale/partial target cache warning 输出具体 target 名称。
- doctor 能为 page target 和 direct data source target 生成可执行 rescan 命令。
- `capture-to-notion capture verify --page-id`。
- apply 后 verification summary。
- Notion official property type regression net。
- no Notion MCP fallback 约束。
- `capture-to-notion capture preflight`。
- generic structure analyzer。
- profile-driven 状态归一化、book defaults、primary data source scoring、normalized record、summary policy。
- summary-like field 缺少主体内容源时阻塞写入，不静默使用页面简介或默认模型总结。
- 默认不再生成 `example.com` fake cover。
- `existing_page_id` 一等输入字段，可直接生成 `update_page` 计划。
- 真实 Notion existing-page update E2E：plan update → confirmed apply → verification true，无需手工 patch plan。

## 不再作为后续实现方式的方向

以下方向已经被当前设计否定，后续不应重新引入：

- 不在 schema、planner、verifier 中根据 Notion 字段名猜业务语义。
- 不在 Capture to Notion 流程中回退使用 Notion MCP。
- 不把 book、cover、author、ISBN、page_count 等业务字段硬编码进通用层。
- 不为了单个业务库提前抽公共框架。
- 不让写入绕过 plan-first 和 confirmation 流程。

## 下一阶段开发计划

### Task A：同步状态文档（已完成，回归维护）

优先级：P0  
对应方向：全部

状态：

- 本文档已建立为当前状态与下一阶段计划的执行基准。
- `PLAN.zh-CN.md` 继续保留为 11 个方向源头。
- 2026-05-15 已同步最新提交 `4786556`、profile-driven/preflight/existing_page_id/E2E 进展和剩余任务。

后续只在方向变化、任务完成或用户纠正实现口径时更新。

### Task B：doctor / rescan 可观测性增强（已完成，回归维护）

必须程度：必须 / 最应该做
优先级：P1  
对应方向：4. 可观测性与调试能力、9. 易用性

目标：

让 stale cache、缺 token、目标未扫描、schema 过期等常见问题给出更明确的下一步命令。

已完成：

- doctor stale/partial target cache warning 输出具体 `target_id`、target title 和 page/data source 标识。
- 能推导时给出建议 rescan 命令。
- page target 使用 `capture-to-notion target scan --page-id ...`。
- direct data source target 使用 `capture-to-notion target scan --data-source-id ...`。
- alias 可用时优先输出 `--alias`，否则回退 `--target-id`。
- 旧 direct data source cache 即使 `target.data_source_id` 缺失，也可从 data source entry 或 key 推导。
- page target 即使缓存中带有 `data_source_id`，doctor 明细也不混入 data source 重扫语义。
- 缺 token、旧配置目录存在、输出不泄露 token 已有回归测试覆盖。
- `logs inspect` 暂不实现；后续只在真实日志排障需求出现时再评估，并保持只读、不泄露 token。

作用：

- 出错时更快定位原因。
- 减少手动判断下一步命令。
- 降低 schema 过期、target 未扫描、alias 不存在时的误操作风险。

回归测试：

- stale `field_sources` cache。
- partial `field_sources` cache。
- page target rescan 命令。
- direct data source target rescan 命令。
- legacy direct data source cache fallback。
- page target 不混入 data source rescan 语义。
- 缺 token。
- 旧配置目录存在。
- 输出不包含 token。

### Task C：补齐 PLAN golden cases

必须程度：关键场景必须做，长尾场景可分批后置
优先级：P1  
对应方向：5. 评估与回归测试、7. 文字正确性与输出质量、8. 页面与视觉正确性

目标：

对照 `PLAN.zh-CN.md` 的 golden case 列表，补齐尚未系统覆盖的场景。

范围：

已补充：

- schema 过期预检：缓存结构中任一 data source 标记 `schema_status: stale` 时阻塞直接 plan、建议 rescan，并在同时命中 risky target 时保留 risky 确认要求。
- 单缺 ISBN golden：schema 与其他关键值齐全但输入缺少 ISBN 时，只提示 `book_key_values_missing:isbn`，不混入作者或页数缺失。
- 封面下载失败 apply golden：下载失败时主写入继续，`asset_results` 回退 external URL，顶层 `warnings` 暴露 `asset_download_failed`。

本轮复核确认已有覆盖，继续回归维护：

- 目标未扫描 / target cache 缺失：preflight、planner、CLI 已覆盖。
- 同名目标：`target search` duplicate title disambiguation 已覆盖。
- 图片或文件 URL 不可访问：verify/apply URL accessibility warnings 已覆盖。
- 高频多作者、中英文标题、作者、ISBN 解析：planner 与 golden 已覆盖主路径，单缺 ISBN 已补充。

可后置覆盖：

- 低频标题格式。
- 边缘字段组合。
- 纯覆盖率型测试。

作用：

- 防止后续改 planner/scanner/writer 时回归。
- 约束字段来源继续来自 parser profile、target cache、write plan 或显式 mapping。
- 让真实 Notion 写入前有本地可重复验证。

测试建议：

- 继续使用本地 cache fixture，不真实写入 Notion。
- 每个新增行为先写失败测试。
- schema 过期预检已用本地 cache fixture 回归覆盖，后续继续保持该方式。
- 保持业务字段来源来自 parser profile、target cache、write plan 或显式 mapping。

### Task D：真实 Notion 端到端验证（部分完成，剩素材/视觉验证）

必须程度：如果继续可靠写书籍封面、page cover、files 字段则必须；只写文字条目可暂时后置
优先级：P1  
对应方向：1. 安全与副作用控制、8. 页面与视觉正确性

已完成：

- 2026-05-15 使用已有页面 `3606a715-808c-8101-8ca5-e0a2258f1e6b` 完成真实 update E2E。
- 输入 JSON 直接包含 `existing_page_id`，plan 自动生成 `update_page`，无需手工 patch。
- confirmed apply 成功，verification `verified: true`，关键字段 `主题`、`状态`、`内容描述` 均存在。
- 全程不使用 Notion MCP。

剩余目标：

验证当前 Skill 是否能在真实 Notion 环境中完成素材相关 scan、plan、apply、verify，并确认 page cover、files 字段和图片 URL 实际可显示。

剩余范围：

- 真实 target scan（仅在 cache 缺失、用户要求或结构过期时）。
- 带 page cover/files 字段的 capture plan。
- capture apply。
- capture verify。
- 验证 page cover、files 字段、关键字段实际可见。
- 主写入成功和素材失败分开说明。

作用：

- 确认封面/图片不是“字段里有 URL 就算成功”。
- 避免计划显示已写入封面，但 Notion 实际不可见。
- 满足书籍 capture 不能只验证标题/状态的要求。

剩余验收标准：

- 真实 Notion 页面可创建或更新。
- page cover 可访问性被验证。
- 计划声明的 files 字段可访问性被验证。
- verification warning 不掩盖 apply 结果。
- 全程不使用 Notion MCP。

注意：

- 这是带外部副作用的验证任务，执行前必须再次确认目标页面、目标数据库和写入内容。

### Task E：版本与迁移治理补齐

必须程度：应该做，但不是当前功能可用性的必须项
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

作用：

- 安全迁移旧配置。
- 避免路径/命名变更导致旧 token、旧 alias、旧配置混乱。
- 为未来给其他人使用或跨机器迁移做准备。

测试建议：

- 旧配置存在，新配置不存在。
- 旧配置和新配置同时存在。
- token 不出现在 stdout/stderr。
- dry-run 不写文件。
- 未确认时不迁移。

### Task F：易用性与 token 成本优化

必须程度：应该做，但建议等核心行为稳定后再做
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

作用：

- 降低日常使用认知负担。
- 减少 Skill 加载 token 成本。
- 降低未来使用 Skill 时抓错重点的概率。
- 让 README、FAQ、Skill 文档职责更清晰。

测试建议：

- 文档命令与 CLI 保持一致。
- `SKILL.md` 精简后仍保留 plan-first、confirmation、no MCP fallback、target scan/plan/apply 主路径。

### Task G：复用规范沉淀

必须程度：可以暂时不做，等第二个类似 Skill 出现后价值更高
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

作用：

- 为未来类似 Skill 复用 plan/apply/doctor/verify/parser profile 等模式提供依据。
- 避免下一个 Skill 重新踩坑。
- 但当前不应提前抽公共框架。

验收标准：

- 只写规范，不抽公共库。
- 至少等第二个 Skill 出现同类需求后再考虑复用库。
- 不影响 `capture-to-notion` 自身稳定性。

## 推荐执行顺序

Task A 和 Task B 已完成，下一阶段剩余 5 个任务。按必须程度排序：

### 必须 / 最应该做

1. Task C：补齐 PLAN golden cases 的关键场景。
2. Task D：真实 Notion 素材/视觉端到端验证（如果近期继续做书籍、封面或 files 字段写入）。

### 应该做，但不急

3. Task E：版本与迁移治理补齐。
4. Task F：易用性与 token 成本优化。

### 可以后置

5. Task G：复用规范沉淀。

如果目标是“自己日常可靠使用”，最低完成 Task C 关键场景回归维护、Task D 素材/视觉验证即可；如果目标是“可维护、可迁移、别人也能用”，再做 Task E 和 Task F。Task G 等出现第二个类似 Skill 或复用需求更明确时再做。

## 当前执行原则

- 涉及代码行为变更时，先写失败测试，再实现。
- 涉及 Notion 写入、迁移、删除、覆盖等外部副作用时，先确认目标和影响范围。
- 具体字段要求来自 target scan 缓存、Notion API 返回结构、写入计划、parser profile 或显式 mapping。
- 通用代码只按 Notion 官方 property object / page property value 类型处理。
- 不回退 Notion MCP；如果 Skill 缺能力，优先修 Skill 自己的 API 封装和流程。
