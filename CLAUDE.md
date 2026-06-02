# Capture to Notion 项目规则

1. Capture to Notion Skill 的目标是替代 Notion MCP；后续 Notion 内容采集、写入、验证和结构读取都应统一走当前 `capture-to-notion`/Skill 后端及其自封装 API。如果 `capture-to-notion` 缺能力或缓存/扫描不准，应先说明能力缺口，并修 Skill 自己的 API 封装和流程，不回退使用 Notion MCP。
2. Capture to Notion 的用户侧运行编排规则应写入 `SKILL.md`；本文件只保留后端实现、测试组织、项目协作和文档分层规则。
3. 设计 Capture to Notion 验证能力时，应保持通用；不要把某个具体业务字段或参数（如作者图片字段）硬编码进 verifier，具体字段要求最多来自 target scan 缓存、计划或显式映射。
4. 当 Capture to Notion 的实现方向被纠正或变更时，必须同步更新相关执行计划、长期计划和文档，避免旧计划继续驱动已否定的实现方向。
5. 设计 Capture to Notion 字段解析与校验时，具体 Notion 字段应来自 target scan 缓存、Notion API 返回结构、写入计划或显式映射；通用代码只按 Notion 官方 API 文档定义的 property object / page property value 类型处理，不在 schema/planner/verifier 中内置业务字段别名或特殊字段逻辑。
6. Capture to Notion 的 scanner/planner 不得通过同页相邻导航数据库、标签过滤或 child_database 猜测写入目标；真实可写 data_source 必须来自现有条目的 parent/data_source、明确扫描结果、view 归属或用户确认的 profile/cache 绑定。
7. Capture to Notion 书籍补全、资产和 relation 的测试不能只验证标题/状态；测试计划和 fixtures 应覆盖 profile/plan 要求的 required fields、asset actions、relation actions 和 verification expectations。
8. Capture to Notion 处理 relation 字段时，不要把 Notion 返回的 `target_database_id` 直接当作 data_source_id；应先通过 database API 读取其 `data_sources`，再用真实 data_source_id 查询/创建关联页。
9. Capture to Notion 后端实现应 cache-first：有可信 target cache 时默认用缓存生成计划；仅在 cache 缺失、用户明确要求重建缓存，或 apply 遇到结构过期类 API 错误后，才重新扫描、更新 cache、重新生成计划，并在有写入副作用时避免重复创建。
10. Capture to Notion planner/compact review 必须暴露具体写入对象：主页面的新建/更新目标、relation 补全目标页，以及新建页尚无 page_id 或 relation 尚未解析等状态；不要只返回目标数据库或字段名。
11. Capture to Notion 的 preflight、structure analyzer 和推荐约束应尽量通用化；不要把具体业务字段、页面名或页面类型写死在代码中，必要的关键词/风险规则应配置化或由扫描结构、cache/profile、显式映射驱动。
12. 执行 Capture to Notion 子任务时，如果审查或验证发现的问题超出当前任务边界、需要改变计划范围，或会引发连续链路修复，不要继续自由修复；应先停下来说明问题、影响范围和可选修改方案，等待用户确认后再继续。
13. Capture to Notion 真实 E2E 测试脚本、测试计划和运行产物应集中在同一测试目录中管理；默认使用 `scripts/e2e/` 放脚本和计划，默认产物放 `scripts/e2e/artifacts/`，不要默认散落到 `/tmp` 或独立 `docs/` 路径。
14. Capture to Notion 暴露出的写入/补全/验证问题应优先归纳为通用能力缺口来设计修复，不要围绕单个书籍、作者或具体对象做一次性补丁；对象级处理只能作为验证案例。
15. Capture to Notion 后端只负责执行能力和不可越过的安全边界，例如拒绝更新 archived/in_trash、parent data_source 不匹配或不存在的页面；不要在后端编码自动新建、恢复、重新搜索、重新定位等场景策略。此类 apply safety failure 应交给 Skill/AI 编排层解释、推荐并重新计划。
16. Capture to Notion 处理 Notion `files` 类型图片字段时，应按实体上传资产处理：计划和确认摘要必须明确 `download_and_attach` / 上传后的文件语义，不要把作者照片、封面等 files 字段描述成单纯保存源 URL。
17. Capture to Notion apply 成功结果和用户汇报必须包含完整写入位置；用户侧默认只汇报最终完整写入路径，不单独列父级目标路径，除非需要消歧或排查；不要只返回或只汇报 page_id / URL。
