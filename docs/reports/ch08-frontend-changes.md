# Ch08 前端 · 改动记录

> 按 [00-change-log-overview.md](./00-change-log-overview.md) 的格式：
> 每节写「新增 / 修改与原因 / 验证」，代码位置用符号名，不用行号。

---

## 0 · 基线核对 + `subgraphs` spike（2026-11-14）

### 新增

- 无产品代码。
- 临时 spike 脚本只放在 `/tmp`，未入库；结论写在本节。

### 修改与原因

无。

### 验证

```bash
uv run pytest -q
# 1030 passed, 17 skipped in 47.04s

uv run ruff check .
# All checks passed!
```

与 `handoff-ch08.md` 的 **16 skipped** 差 1 条，属于环境差异（不是失败）；
基线以本次实测的 **1030 passed / 17 skipped** 为准。

### Spike 目标

验证项目图在 LangGraph 1.2.18 下开：

```python
graph.astream(state, stream_mode="updates", subgraphs=True)
```

能否拿到子图内部节点事件，以及顶层 `rules` 的最终 update 是否还在。

### 方法

- 用真实 `build_graph()`；
- 只把模型/数据库相关节点替换成确定性 stub，保留真实图结构与边；
- 不调 LLM、不查库，避免花钱和环境污染；
- 分别跑 `subgraphs=False` / `subgraphs=True`，记录 chunk。

### 结果

| 观察 | `subgraphs=False` | `subgraphs=True` |
|---|---|---|
| 顶层节点 | `resolve_reference` / `classify_intent` / `rules` | 相同 |
| 子图内部节点 | **无** | `intake` / `extract` / `validate` / `query_data` / `judge` / `compute` / `compliance` / `respond` |
| 追问路径 | — | `intake` / `need_info`，`pending_question=order_id` |
| 顶层 `rules` 最终 update | 有，包含完整子图输出 | **仍有**，包含完整子图输出 |
| `final` 关键字段 | `answer` / `refund_amount` / `compliance_violation` | 相同 |

关键 update 字段：

| 内部节点 | update 里的字段 | 用途 |
|---|---|---|
| `query_data` | `order_snapshot` / `evidence` | 进度：已核对订单 |
| `compute` | `refund_amount` | 进度：已算出金额 |
| `compliance` | `compliance_violation` | 判断是否允许展示金额 |
| `need_info` | `pending_question` | 进度：等待用户补充 |
| `judge` | `refund_judgement` | 判断 `eligible` |
| `respond` | `answer` / `is_placeholder` | 最终回答 |

### 决策

**真实 `progress` 可行，不走降级方案。**

- `subgraphs=True` 能拿到真实子图节点事件；
- 顶层 `rules` update 仍在，`final` 不需要改数据来源；
- 内部 update 只用于 `progress`，**不能写进 `final`**，否则会把中间状态误当最终回答。

### 对实现的影响

1. `app/api/chat.py` 的 `<engine>.astream(...)` 增加 `subgraphs=True`；
2. chunk 从 `{node: update}` 变成 `(namespace, update)`：
   - `namespace == ()`：顶层，继续 `final.update(update)` / `record_span`；
   - `namespace != ()`：子图内部，只用于 `progress`，不写 `final`、不重复记 span。
3. 测试里的 `FakeGraph.astream` 要接受 `subgraphs` 参数；
4. 现有帧顺序断言要允许 `progress` 帧；
5. 真实延迟不在 stub 上测；Task 5/12 用 `turn:persist` / `turn:total` span 观察。

### 未验证 / 边界

- 本次 spike 没有跑真实 LLM/Chroma/DB，因此**不测真实延迟**；
- `subgraphs=True` 对 `record_span` 的重复记录风险，在 Task 5 按“只记顶层”处理；
- 性能结论留给 Task 5 的 span 数据，不用 stub 耗时下结论。

---

## 1 · 数据层：`reply_to_client_message_id` + 迁移（2026-11-14）

### 新增

| 文件 | 内容 |
|---|---|
| `scripts/migrate_ch08.py` | 老库幂等迁移：补列 + 唯一部分索引；`--db` 指定库 |
| `tests/db/test_migrate_ch08.py` | 旧 schema、列已存在但索引缺失、重复执行幂等 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/models.py › Message` | 只有 `client_message_id`；助手回复没有关联字段 | 增加 `reply_to_client_message_id` + `uk_msg_reply` 唯一部分索引 | 重试回放必须按 `(conversation_id, client_message_id)` 精确查助手回复；靠消息顺序猜不可靠 |
| `app/db/schema.sql › messages` | 同上 | 加列 + 唯一部分索引；`meta` 注释补 `actions/signals` | `schema.sql` 与 ORM 双写必须一致 |
| `app/db/repo_message.py › append_message` | 只能按 `client_message_id` 做用户消息幂等 | 增加 `reply_to_client_message_id`；撞 `uk_msg_reply` 时返回已有助手 id | 并发重试不能写出两条助手回复 |
| `app/db/repo_message.py › get_reply_by_client_message_id` | 无 | 按 `(cid, reply_to_client_message_id)` 查助手消息 | Task 3 回放要用；错误消息不写该字段，不会被回放 |
| `app/core/history.py › save_assistant_message` | 只透传 `meta/status` | 透传 `reply_to_client_message_id` | API 层保存时能建立关联 |
| `scripts/init_db.py › diff` | 只检查 `schema.sql` 独有索引 | 补 `orm_idx - sql_idx` | 防止只在 `models.py` 加索引、`schema.sql` 漏写 |
| `tests/db/test_repo_message.py` | 只有用户消息幂等测试 | 新增关联、唯一、作用域 3 个测试 | 覆盖 Task 1 的新契约 |

### 验证

```bash
uv run pytest tests/db/test_repo_message.py tests/db/test_migrate_ch08.py tests/db/test_schema_sync.py -q
# 13 passed

uv run pytest -q
# 1035 passed, 17 skipped

uv run ruff check .
# All checks passed

uv run python scripts/init_db.py
# schema.sql 与 ORM 一致（表 / 列 / 索引）

uv run python scripts/migrate_ch08.py --db data/app.db
uv run python scripts/migrate_ch08.py --db data/eval.db
# 两个库均「已迁移」；再次执行输出「已是最新」
```

### 边界

- 旧消息/用户消息的 `reply_to_client_message_id` 为 `NULL`，不影响历史展示。
- 错误助手消息不写该字段，重试不会回放错误结果。
- 唯一约束是「同一会话内一条用户消息最多一条助手回复」；不同会话可复用同一 `client_message_id`（`ack` 丢失的孤儿边界见 spec §7.4）。
- 本 task 不改 `app/api/chat.py`；保存与回放在 Task 2/3。

---

## 2 · 助手 meta + 先落库再展示 + fail-closed（2026-11-15）

### 新增

- 无新文件；新增测试写在 `tests/api/test_chat_sse.py`。

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/api/chat.py › _run_turn_locked` | 先 `yield delta/citations/actions`，再保存 assistant + flow；保存失败只记日志，仍发 `done` | 同一事务先保存 assistant + flow，成功后才 yield；失败抛 `internal_error`，不发 `delta` | C2-lite 要求“看到即已存”。旧顺序会出现：用户看到了、刷新没了、重试还可能换措辞 |
| `app/api/chat.py › assistant meta` | 只有 `intent/route/citation_ids/resumed` | 增加 `actions` + `signals{amount,pending_question,refund_eligible}` | 刷新后要恢复按钮、金额、追问状态 |
| `app/api/chat.py › spans` | 只有 `turn:total` | 增加 `turn:persist`（`duration_ms/persisted/flow_action/error`）；`turn:total` 补 `persisted/persist_ms` | 证明新增落库没有拖慢首字；失败时能定位到 persist |
| `tests/api/test_chat_sse.py` | 没有 meta / fail-closed 断言 | 新增 3 个测试；span 顺序更新 | 覆盖新契约 |

### 验证

```bash
uv run pytest tests/api/test_chat_sse.py -q
# 34 passed

uv run pytest -q
# 1038 passed, 17 skipped

uv run ruff check .
# All checks passed
```

### 关键行为

- **成功**：保存 assistant + flow → `delta` → `citations` → `actions` → span → `done`。
- **失败**：`ack`/`route` 已发；不发 `delta`；抛 `internal_error`，由 `main.py` 转成 `error` 帧；
  用户消息保留，重试可重跑。
- `meta.signals.amount` 只在以下条件全满足时写：
  `route == rules` + `refund_judgement.eligible` + `refund_amount > 0` + `compliance_violation is None`。
- 错误助手消息仍不写 `reply_to_client_message_id`（Task 1 约定），重试不会回放错误结果。

### 边界

- 本 task **不做回放**（Task 3）；**不做 `progress` 帧**（Task 5）。
- 保存失败时用户看不到已生成回答，这是刻意 fail-closed 的代价。
- 并发下 `save_assistant_message` 可能返回已有 reply id；精确回放在 Task 3 处理。

---

## 3 · 按 `client_message_id` 回放助手回复（2026-11-15）

### 新增 / 修改

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/repo_message.py › get_user_message_by_client_id` | 无 | 按 `(cid, client_message_id, role=user)` 查用户消息 | 回放的 `ack` 需要原来的 `user_message_id` |
| `app/api/chat.py › _parse_assistant_meta` | 无 | 安全解析助手 `meta` JSON | 坏 meta 只影响回放展示，不能让聊天失败 |
| `app/api/chat.py › _replay_frames` | 无 | 从 `meta` 回放 `ack/route/delta/citations/actions/done` | 重试不能重新跑图、不能再写一条回答 |
| `app/api/chat.py › _run_turn_locked` | 槽位填充 → 跑图 → 保存 | 锁内先查 assistant reply；命中就回放并 `return` | 同一 `client_message_id` 不能重复执行 |
| `tests/api/test_chat_sse.py` | `FakeGraph` 没有调用计数 | 加 `calls`；新增 3 条回放测试 | 覆盖命中、未命中、错误消息三条路径 |

### 验证

```bash
uv run pytest tests/api/test_chat_sse.py -q
# 37 passed

uv run pytest -q
# 1041 passed, 17 skipped

uv run ruff check .
# All checks passed
```

### 关键行为

- **命中**：`ack`（原 user id）→ `route`（从 meta）→ `delta`（已存 content）→
  `citations/actions`（从 meta）→ `turn:replay` span → `done`（原 assistant id）。
- **未命中**：正常跑图。用户消息已存在但还没有助手回复时，**仍然重跑**——
  判断依据是“有没有助手回复”，不是“有没有用户消息”。
- **错误消息**：没有 `reply_to_client_message_id`，不会被回放。
- 回放不跑图、不改 flow、不再写库。

### 边界

- 只按同一 `conversation_id` 查；跨会话不查（`ack` 丢失的孤儿边界见 spec §7.4）。
- 回放的 `progress` 帧留给 Task 5 统一补；当前回放先恢复 route/回答/引用/动作。
- 如果第一次请求还在跑，重试会撞会话锁返回 429；等第一次完成后再重试才会回放。

---

## 4 · 会话列表 + 历史消息接口（2026-11-15）

### 新增

| 文件 | 内容 |
|---|---|
| `app/api/conversations.py` | `GET /api/conversations` 与 `GET /api/conversations/{cid}/messages` |
| `tests/api/test_conversations_api.py` | 列表、404、字段白名单与 tool 消息过滤 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/repo_conversation.py › list_conversation_summaries` | 只有 `list_conversations`，返回 ORM 会话，没有标题 | 只读视图：标题取第一条非空用户消息，截断 30 字；带 `active_flow/pending_question` | 前端列表需要标题和未完成流程标记，不能拿 ORM 全量字段 |
| `app/main.py` | 只注册 chat / eval 路由 | 注册 conversations 路由 | 前端刷新/换设备需要历史接口 |
| `tests/db/test_repo_message.py` | 只有会话创建/流程测试 | 增加标题、排序、用户隔离、软删、limit、截断测试 | 覆盖列表查询的真实 SQL |
| `app/api/conversations.py › _assistant_item` | 无 | 只输出 `intent/route/citation_ids/resumed/actions/signals` | 防止 `tool_code`、`trace_id`、`refund_judgement` 等内部字段泄露 |

### 验证

```bash
uv run pytest tests/db/test_repo_message.py tests/api/test_conversations_api.py -q
# 14 passed

uv run pytest -q
# 1046 passed, 17 skipped

uv run ruff check .
# All checks passed
```

### 接口行为

- `GET /api/conversations`
  - 当前 `demo_user_id`；
  - 未软删、`updated_at` 倒序、最多 20 条；
  - `title` = 第一条非空用户消息（截断 30 字），没有则 `"新会话"`；
  - 不返回 `summary` / `draft`。
- `GET /api/conversations/{cid}/messages`
  - 不存在 / 不属于本人 / 已软删 → **统一 404**；
  - 只返回 `user` / `assistant`，**tool 消息永不返回**；
  - `meta` 白名单：user 只给 `selected_action`；assistant 只给
    `intent/route/citation_ids/resumed/actions/signals`；
  - 返回 `client_message_id` / `reply_to_client_message_id` 供前端对账；
  - 错误助手消息照常返回，`status="error"`，没有 `reply_to`。

### 边界

- Ch08 不做分页、搜索、重命名、删除；只读最近 20 条。
- 列表没有单会话 404：按 `user_id` 过滤，别人的会话不会出现。
- 历史接口不返回 `draft/order_snapshot/refund_judgement/confidence` 等内部字段。

---

## 5 · `progress` 帧（子图事件）（2026-11-15）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/progress.py` | 节点 → 语义 stage 映射；最终状态优先级；`STAGE_LABELS` 中文对照；纯函数、可单测 |
| `tests/core/test_progress.py` | 规则/数据/知识/兜底/转人工的 stage、金额抑制、最终优先级、白名单字段、中文对照完整性 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/api/chat.py › _run_turn_locked` | `astream(..., stream_mode="updates")` 只看顶层节点；没有 progress 帧 | `subgraphs=True`；`namespace != ()` 的内部 update 只写 `seen` 并映射 progress；`final` 仍只吃顶层 update | 进度面板需要 `query_data/compliance/retrieve/generate` 这些子图节点；只看顶层只能等子图整体结束 |
| `app/api/chat.py › span` | 每个 chunk 都推进 `node_started` | 只有顶层 chunk 推进 | 子图内部 chunk 会把 `node:rules` 的 duration 截短 |
| `app/api/chat.py › final progress` | 无 | 落库成功后、`delta` 前发最终进度；与上一条相同则跳过 | 前端需要知道“等待确认/等待补充/转人工”；重复帧没必要 |
| `app/api/chat.py › _replay_frames` | 回放没有进度 | 从 `meta.signals/actions` 还原最终进度 | 重试回放应与正常轮次看到相同的面板状态 |
| `tests/api/test_chat_sse.py` | `FakeGraph` 只吐 flat chunk | 支持 `subgraphs` 与 `(namespace, update)`；新增 2 条 progress 测试 | 验证规则/追问两条进度路径 |
| `tests/security/test_scenarios.py` | FakeGraph 不接受 `subgraphs` | 接受并包装 flat chunk | `run_turn` 现在总是开 `subgraphs=True` |

### 验证

```bash
uv run pytest tests/core/test_progress.py tests/api/test_chat_sse.py tests/security/test_scenarios.py -q
# 51 passed

uv run pytest -q
# 1055 passed, 17 skipped

uv run ruff check .
# All checks passed
```

### 关键行为

- chunk 形状：`(namespace, update)`；
  - `namespace == ()`：顶层，写 `final`、记 span、可能发 `route`；
  - `namespace != ()`：子图内部，只写 `seen`、只发 `progress`。
- 规则退款：`checking_order` → `amount_ready` → `waiting_confirm`；
  追问：`waiting_info`；转人工/合规失败：`handoff`。
- 金额只在“可退 + 合规通过 + 金额 > 0”时出现；`bool` 不会被当成金额。
- 同一进度不重复发：`last_progress` 对完整帧做比较；最终进度相同则跳过。
- 回放路径也会先发最终 progress，再发 `delta`。
- 帧里只有 `event/stage/status`，必要时加 `amount/pending_question`；
  不含节点名、规则 id、置信度。
- `STAGE_LABELS` 提供每一步的中文名称（如 `amount_ready` → 「已算出金额」），
  只作为文档/测试/前端文案参考，**不随 SSE 下发**。

### 边界

- 仍不做 token 级流式；`progress` 是等待期间的反馈。
- 映射是纯函数，未知节点返回 `None`；不会因为“没有对应 stage”而报错。
- 前端中文文案在 Task 9 做；后端只给语义 key。
- 真实延迟未在本 task 测量；`turn:persist` / `turn:total` span 已就绪，验收时看 P50/P95。

---

## 6 · 转人工结构化入口（2026-11-15）

### 新增

- 无新文件；测试写在 `tests/api/test_chat_sse.py`。

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/api/chat.py › _finalize_answer` | 守卫/落库/发帧逻辑只存在于正常图路径 | 抽成共享 async generator；正常路径和转人工短路共用 | 避免两套保存、fail-closed、span 逻辑漂移 |
| `app/api/chat.py › _run_turn_locked` | 只有文本消息进槽位/图 | 在回放检查之后、槽位之前短路 `selected_action.type == "transfer_human"` | 按钮点击要确定性转人工，不能靠再分类 |
| `app/api/chat.py › 转人工分支` | 无 | 保存用户消息 → `ack` → `route=human` → `human_exit` 固定话术 → 清空流程 → 落库 → `progress(handoff)` → `delta` → `actions` → `done` | 前端要能点、后端要真的转，且不建工单 |
| `tests/api/test_chat_sse.py` | 无转人工结构化测试 | 新增 2 条：不跑图/不建单/清空流程；同 id 重试回放 | 覆盖 Task 6 的验收点 |

### 验证

```bash
uv run pytest tests/api/test_chat_sse.py -q
# 41 passed

uv run pytest -q
# 1057 passed, 17 skipped

uv run ruff check .
# All checks passed
```

### 关键行为

- 请求：`selected_action={"type": "transfer_human"}`；`message` 可以为空。
- 顺序：**回放检查 → 转人工短路 → 槽位填充**。
- 复用 `app/graph/nodes.py › human_exit` 的固定话术与 `transfer_human` 动作；
  **不跑图、不建 `tickets` 行**。
- `decide_flow` 对 `route=human` 会清空当前流程（退款/查询都清）。
- 保存顺序与正常路径一致：先同事务保存 assistant + flow，再发 `delta`。
- 同一个 `client_message_id` 重试走 Task 3 的回放，不重复写。

### 边界

- 只做“通知转接”，没有人工坐席队列/工单闭环；这是 Ch08 明确的非目标。
- 不做“转人工原因收集”；原因只在 trace/落池侧有，不扩展前端表单。
- 其他结构化动作（`select_order` / `select_sku` / `select_spec` / `resume_flow`）仍走槽位填充，不受影响。

---

## 7 · 前端脚手架 + 请求层（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/package.json` | React 18 + Vite 5 + TypeScript + Vitest；`engines.node >= 20` |
| `client/vite.config.ts` | React 插件；`/api` proxy → `127.0.0.1:8080`；Vitest 用 jsdom |
| `client/tsconfig.json` | strict / noUnusedLocals / noUnusedParameters |
| `client/index.html`、`src/main.tsx`、`src/App.tsx`、`src/styles.css` | 最小可启动壳 |
| `client/src/lib/base.ts` | `BASE`、`ApiError`、`errorForStatus`、`newTraceId`、`request` |
| `client/src/lib/sse.ts` | `sendChat` / `readSse` / `drainFrames`：分块、CRLF、keepalive、坏 JSON |
| `client/src/lib/api.ts` | `listConversations` / `getMessages` |
| `client/src/lib/base.test.ts` | 错误映射、trace_id、request 头 |
| `client/src/lib/sse.test.ts` | 完整帧、半帧、CRLF、多行 data、坏 JSON、分块流、POST body、429 |
| `client/.nvmrc`、`client/.gitignore` | Node 20；`node_modules/` / `dist/` 不入库 |

### 修改与原因

无后端代码改动。

### 验证

```bash
# 本机默认 node 是 v14，Vite 5 不支持；测试用 nvm 的 Node 20.20.2
PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client install
# added 167 packages

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run typecheck
# 通过

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run test
# 2 files, 12 tests passed

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run build
# vite v5.4.21；dist JS 142.76 kB（gzip 45.96 kB）

# dev 冒烟
# VITE ready in 212 ms；GET http://127.0.0.1:5173/ → HTTP 200
```

**环境注意**：本机默认 `node` 是 `v14.20.0`，Vite 5 需要 Node 18/20+。
用 nvm 切到 `20.20.2` 后通过；`client/.nvmrc` 已写 `20`。

### 设计要点

- 组件层不允许直接 `fetch`，只能调 `lib/api.ts` / `lib/sse.ts`；
- SSE 解析与 UI reducer 解耦，方便 Task 8 单测；
- 坏帧只跳过当前帧，不让整条流崩掉；
- 404 / 429 / 5xx 统一映射成 `ApiError`；
- Vite proxy 解决跨端口，不改后端 CORS。

### 边界

- `App.tsx` 仍是占位页；聊天窗、reducer 在 Task 8；
- 还没接真实后端联调；Task 8/10 做；
- 不引入 Playwright；组件测试在 Task 8/9 用 RTL。

---

## 8 · 聊天窗 + 帧 reducer + 错误处理（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/state/turn.ts` | 帧 reducer：begin/retry/frame/fail/abort/reset；消息、进度、错误 |
| `client/src/state/turn.test.ts` | 11 条 reducer 测试：帧顺序、delta 累积、未知帧/stage、waiting 终态、错误、重试、重置 |
| `client/src/components/ChatWindow.tsx` | 消息列表、空状态、处理中提示、错误横幅 + 重试 |
| `client/src/components/MessageBubble.tsx` | 纯文本气泡 + `[1]` 引用角标（tooltip 来源标题） |
| `client/src/components/Composer.tsx` | 输入框、发送/停止、Enter 发送、Shift+Enter 换行、500 字上限 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/App.tsx` | 占位页 | `useReducer` + `sendChat`；发送/重试/停止/错误映射/localStorage | Task 8 要把 SSE 帧真正接到界面 |
| `client/src/styles.css` | 只有标题样式 | 聊天布局、气泡、输入框、错误横幅、移动端 | 可演示界面需要基本布局 |

### 验证

```bash
PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run typecheck
# 通过

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run test
# 3 files, 23 tests passed

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run build
# vite v5.4.21；dist JS 640.54 kB（gzip 206.34 kB）
# 提示 chunk >500 kB（antd 体积；Task 12 再评估 code split）

# dev 冒烟
# VITE ready in 239 ms；GET 127.0.0.1:5173 → HTTP 200
```

### 关键行为

- **发送**：生成 `client_message_id` 并写 `localStorage`；流式期间禁用发送。
- **停止**：`AbortController.abort()`；不会弹错误横幅；输入保留。
- **错误**：
  - 404 → 清空当前会话 + 「会话不存在，请重新开始」；
  - 429 → 提示 + 禁用发送 5 秒；
  - 5xx/网络/SSE `error` 帧 → 保留输入，显示重试；
  - SSE `error` 帧会让 `sendChat` 以 `ApiError` 结束，避免误清输入。
- **重试**：复用最后一条用户消息和同一个 `client_message_id`，不再新增用户气泡。
- **渲染**：纯文本 + `white-space: pre-wrap`；`[1]` 角标用 `citation_ids` 的来源标题做 tooltip；不使用 `dangerouslySetInnerHTML`。
- **未知帧/未知 stage**：忽略；`waiting` 是终态，不被后续 `done` 覆盖。

### 8.1 Ant Design 组件替换（2026-11-16）

按“优先用 antd，组件样式不支持再自己实现”的要求：

| 位置 | 使用 antd 组件 | 保留自定义的部分 |
|---|---|---|
| `main.tsx` | `ConfigProvider` + `antd/dist/reset.css` | — |
| `App.tsx` | `Flex` / `Typography` / `Tag` | 页面整体布局 |
| `ChatWindow` | `Card` / `Alert` / `Empty` / `Spin` / `Button` / `Flex` | 消息列表滚动区 |
| `Composer` | `Card` / `Form` / `Input.TextArea` / `Button` / `Space` / `Flex` | — |
| `MessageBubble` | `Flex` / `Avatar` / `Typography` | **聊天气泡本身**：antd 没有对应组件，保留自定义样式 |

依赖：`antd@^5.29.3`。

构建体积从 150.60 kB 增到 **640.54 kB（gzip 206.34 kB）**，Vite 提示超过 500 kB。
暂不处理，Task 12 收尾时再评估是否做 code split；当前演示优先组件一致性。

### 8.2 端口调整（2026-11-16）

按机器级端口表（`baby-ai/README.md` + `baby-ai/scripts/devguard.mjs`）重新分配：

| 服务 | 旧端口 | 新端口 | 原因 |
|---|---:|---:|---|
| ecom FastAPI | 8080 | **8090** | 8080 是常见默认端口，且未登记在全机表里 |
| ecom 评测实例 | 8081 | **8091** | 8081 与 shopkeeper-agent 的 embedding 服务冲突 |
| ecom 前端 Vite | 5173 | **5190** | 5173 与 evalflow / shopkeeper / deepsearch 前端冲突 |
| Chroma | 8000 | 8000 | 共享依赖，不动 |
| Ollama | 11434 | 11434 | 系统级服务，不动 |

前端同时加 `strictPort: true`：端口被占直接失败，不再静默顺延到 5191/5192。

同步更新：

- `client/vite.config.ts`（5190 + `strictPort` + proxy 8090）
- `README.md`、`.env.example`、`.env`
- `docs/reports/local-env.md`、`handoff-ch08.md`、`manual-checks.md`
- `docs/superpowers/specs/` 的 ch02 / ch07 / ch08 与对应 plans
- `scripts/eval_e2e.py` 默认 8091
- 跨仓库：`evalflow/backend/config.py`、`evalflow/.env.example`
- 机器级表：`baby-ai/README.md`、`baby-ai/scripts/devguard.mjs`、`evalflow/scripts/devguard.mjs` 的 neighbors

历史 run 报告（run-01/02/03、run-local-*）保留当时的 8081，不回写。

验证：

```bash
uv run pytest -q
# 1060 passed, 14 skipped（总数 1074；与上次 1057/17 相比，
# 3 条 skip 变为 pass；本次未改后端业务代码）
uv run ruff check .
# All checks passed

npm --prefix client run typecheck / test / build
# 23 tests passed；build 640.54 kB（gzip 206.34 kB）

# 冒烟
# GET 127.0.0.1:5190 → HTTP 200
# GET 127.0.0.1:8090/api/health → {"status":"ok"}
```

### 边界

- 进度面板（`state.progress`）和动作卡片还没渲染；Task 9。
- 历史恢复/会话列表/断流对账在 Task 10/11。
- 还没有真实后端联调；Task 8 用单测覆盖 reducer 与请求层，Task 12 做四场景手工验收。
- 输入保留策略：成功才清空；错误时保留原文本。

### 下一步

- Task 9：进度面板 + 动作卡片 + 转人工按钮。
