# Capture to Notion 项目规则

1. Capture to Notion Skill 的目标是替代 Notion MCP；后续 Notion 内容采集、写入、验证和结构读取都应统一走当前 `capture-to-notion`/Skill 后端及其自封装 API。不要在 Capture to Notion Skill 流程中回退使用 Notion MCP；如果 `capture-to-notion` 缺能力或缓存/扫描不准，应先修 Skill 自己的 API 封装和流程，除非用户明确要求临时使用 MCP。
2. 设计 Capture to Notion 验证能力时，应保持通用；不要把某个具体业务字段或参数（如作者图片字段）硬编码进 verifier，具体字段要求最多来自 target scan 缓存、计划或显式映射。
3. 当 Capture to Notion 的实现方向被纠正或变更时，必须同步更新相关执行计划、长期计划和文档，避免旧计划继续驱动已否定的实现方向。
4. 设计 Capture to Notion 字段解析与校验时，具体 Notion 字段应来自 target scan 缓存、Notion API 返回结构、写入计划或显式映射；通用代码只按 Notion 官方 API 文档定义的 property object / page property value 类型处理，不在 schema/planner/verifier 中内置业务字段别名或特殊字段逻辑。
5. Capture to Notion 写入 Notion 页面中的某个可视区域/列表（如“在读列表”）时，不要把同页的导航数据库、标签过滤或相邻 child_database 当作目标；必须通过现有条目的 parent/data_source 或明确扫描结果确认真实可写 data_source 后再计划和写入。
6. Capture to Notion 书籍测试或真实写入若目标是“信息补全”，不能只验证标题/状态；必须在计划中明确封面、作者、ISBN、页数等关键字段的写入状态，无法写入的字段要先说明阻塞原因并等待确认。
7. Capture to Notion 处理 relation 字段时，不要把 Notion 返回的 `target_database_id` 直接当作 data_source_id；应先通过 database API 读取其 `data_sources`，再用真实 data_source_id 查询/创建关联页。
8. Capture to Notion 写入流程应 cache-first：有可信 target cache 时直接用缓存生成计划并写入，不要默认重新扫描；仅在 cache 缺失/用户明确要求重建缓存，或 apply 遇到结构过期类 API 错误后，才重新扫描、更新 cache、重新生成计划，并在有写入副作用时避免重复创建。
9. Capture to Notion 生成写入计划时必须列出将写入的具体页面：主页面的新建/更新目标、relation 补全目标页；若新建页尚无 page_id 或 relation 尚未解析，必须在计划中明确说明，而不是只展示目标数据库或字段。
10. Capture to Notion 的 Skill 推荐流程应先判断用户意图：直接写入、需要用户补充信息、还是需要联网搜索补全；有明确目标页面时以该页面结构为主，无目标时先给目标建议，再进入补全与写入计划。
11. Capture to Notion 处理外部 URL 时，不要默认自动解析或抓取；URL 也应进入推荐动作，由推荐层结合目标结构、输入意图和成本判断是否建议解析/补全，并在需要时让用户确认。
12. Capture to Notion 的 preflight、structure analyzer 和推荐约束应尽量通用化；不要把具体业务字段、页面名或页面类型写死在代码中，必要的关键词/风险规则应配置化或由扫描结构、cache/profile、显式映射驱动。
13. 执行 Capture to Notion 子任务时，如果审查或验证发现的问题超出当前任务边界、需要改变计划范围，或会引发连续链路修复，不要继续自由修复；应先停下来说明问题、影响范围和可选修改方案，等待用户确认后再继续。
