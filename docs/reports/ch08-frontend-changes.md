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

---

## 9 · 进度面板 + 动作卡片 + 转人工（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/components/ProgressPanel.tsx` | antd `Steps` / `Card` / `Grid`：规则/知识/数据/兜底/转人工的步骤；移动端折叠成状态条 |
| `client/src/components/ActionBar.tsx` | antd `Card` / `Button` / `Space`：转人工、订单/型号/规格卡片、继续流程 |
| `client/src/lib/format.ts` | 分 → 元：`formatAmount` |
| `client/src/components/actions.test.tsx` | 7 条：转人工、订单/型号/规格 payload、继续流程、禁用、未知动作 |
| `client/src/components/progress.test.tsx` | 5 条：退款三态、等待补充、转人工、知识步骤、金额格式化 |
| `client/src/test/setup.ts` | RTL cleanup + jsdom `matchMedia` mock |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `App.tsx` | 只有聊天窗/输入框 | `workspace` 两栏；接入 `ProgressPanel` / `ActionBar`；`handleAction` | 进度和动作需要可见、可点击 |
| `state/turn.ts` | 不保存 `selected_action` | 增加 `lastSelectedAction`，重试时复用 | 结构化动作重试不能退化成自然语言 |
| `App.tsx › visibleActions` | 无 | 最后一个 assistant 的动作；转人工成功后过滤掉重复 transfer 按钮 | 转人工回复本身也带 transfer_human，不能无限重复 |
| `styles.css` | 单栏聊天 | 两栏 + 侧栏 + 移动端堆叠 | 进度/动作需要独立区域 |
| `vite.config.ts` | 无 setupFiles | 加 `src/test/setup.ts` | antd 响应式组件需要 `matchMedia`；RTL 需要 cleanup |

### 验证

```bash
PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run typecheck
# 通过

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run test
# 5 files, 36 tests passed

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run build
# vite v5.4.21；dist JS 685.61 kB（gzip 218.35 kB）

# dev 冒烟
# VITE ready in 248 ms；GET 127.0.0.1:5190 → HTTP 200
```

### 关键行为

- 退款：`已核对订单` → `已算出金额 ¥1249.00` → `等待您确认`；
  追问时替换为 `等待您补充信息`；转人工时显示 `正在转接人工`。
- 金额只来自 `progress.amount`，前端不自己算。
- 动作 payload：
  - `select_order` → `{type:"select_order", order_id}`
  - `select_sku` → `{type:"select_sku", sku_id}`
  - `select_spec` → `{type:"select_spec", spec}`
  - `resume_flow` → 文本「继续退费」或「继续」
  - `transfer_human` → `{type:"transfer_human"}`
- 重试结构化动作时复用 `lastSelectedAction`，不会退化成纯文本。
- 转人工成功后，最后一个用户消息是「转人工」，重复的 transfer 按钮会被过滤。

### 边界

- 历史恢复/会话列表在 Task 10；断流对账/重连在 Task 11。
- 进度面板只显示语义步骤，不显示节点名/规则 id/置信度。
- 移动端通过 antd `Grid` 断点折叠成一行状态条；完整步骤在桌面显示。
- 构建仍超过 500 kB（antd）；Task 12 再评估 code split。

---

## 10 · 历史恢复 + 会话列表（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/components/ConversationList.tsx` | antd `Card` / `List` / `Tag`：标题、更新时间、未完成流程标记、新对话 |
| `client/src/state/reconnect.ts` | `readConversationId`（URL → localStorage）+ 历史接口 → `TurnState` |
| `client/src/state/reconnect.test.ts` | 7 条：URL/存储优先级、历史映射、等待/转人工、会话隔离 |
| `client/src/components/conversations.test.tsx` | 2 条：列表顺序/点击切换、新对话 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `App.tsx` | 只有当前内存会话 | 启动时拉会话列表；按 URL/localStorage 选会话；点击切换；新对话清状态 | 刷新/换设备后要能恢复 |
| `state/turn.ts` | 没有历史动作 | 新增 `history` action 和 `isAssistantAction` 导出 | 历史接口返回的 assistant meta 要还原动作 |
| `App.tsx › runChat` | ack 只更新 reducer | ack 写 `lastConversationId` + URL `?c=`；done 后刷新列表 | 新会话要出现在列表里，刷新后能找回 |
| `styles.css` | 两栏 | 三栏：会话列表 / 聊天 / 进度+动作；移动端堆叠 | 列表需要独立区域 |

### 验证

```bash
PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run typecheck
# 通过

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run test
# 7 files, 45 tests passed

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run build
# vite v5.4.21；dist JS 806.54 kB（gzip 254.28 kB）

# 联调冒烟（后端 8090 + 前端 5190）
# GET 127.0.0.1:5190/api/conversations → HTTP 200（Vite proxy 正常）
```

### 关键行为

- 启动恢复优先级：URL `?c={id}` → `localStorage.lastConversationId` → 最近一条会话 → 新会话。
- 历史接口消息 → UI：
  - user：`client_message_id` 保留，供重试；
  - assistant：`citation_ids` → 角标，`actions` → 动作卡片；
  - `signals.amount/pending_question` → 进度面板最终状态；
  - 最后一条 user 的 `selected_action` → `lastSelectedAction`。
- 会话列表：按接口顺序展示最近 20 条，标未完成退款/查询；点击切换；新对话清空当前状态和 URL。
- 新会话在 `ack` 后写 URL/localStorage，`done` 后刷新列表。
- 切换会话不会串消息；`buildHistoryState` 是纯函数。

### 边界

- 断流对账/重试合并在 Task 11；本 task 只处理已完成历史。
- 列表只读，不做搜索/重命名/删除；最多 20 条（后端限制）。
- 移动端会话列表放在顶部（最高 180px），不单独做抽屉。
- 历史加载失败不阻塞新对话；404 会清空并提示重新开始。

### 下一步

- Task 11：断流重连（C2-lite）。

---

## 11 · 断流重连（C2-lite）（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/state/reconnect.ts › reconcileHistory` | 按 `reply_to_client_message_id === 本地 client_message_id` 判断服务端是否已保存本轮回复；命中则整段替换，未命中返回 `pending` |
| `client/src/state/reconnect.test.ts` | 4 条：命中替换、未保存、不同 id、error 消息不算完成 |
| `client/src/state/turn.test.ts` | 3 条：新一轮不追加到上一轮回答、重试丢掉未完成草稿、interrupt 保留本地内容 |
| `client/src/components/chat-window.test.tsx` | 2 条：普通错误按钮为“重试”；断流状态按钮为“继续” |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `App.tsx › runChat` | 流结束就返回，没有 `done` 也不对账 | 记录 `sawDone`；没有 `done` 或网络断开时调用 `recoverAfterDisconnect` | 断流后服务端可能已保存回答，直接用本地半截内容会跳变 |
| `App.tsx › recoverAfterDisconnect` | 无 | 拉 `GET /messages`，命中则 `history` 替换；未命中显示“继续”；历史失败保留本地并报错 | 这是 C2-lite 的核心：历史对账 + 同幂等键重试 |
| `App.tsx › handleStop` | abort 后没有任何提示 | abort 后由 catch 落 `interrupt`，按钮变“继续” | 用户停止也要能用同一个 id 继续 |
| `state/turn.ts › TurnState` | 没有断流标记 | 增加 `interrupted`；`interrupt` action 保留消息/进度 | 区分“可继续的断流”和普通错误 |
| `state/turn.ts › appendDelta/patchAssistant` | 总是找最后一条 assistant | 只找当前用户消息之后的 assistant | 多轮时会把新回答追加到上一轮回答上 |
| `state/turn.ts › retry` | 保留未完成 assistant 草稿 | 丢掉当前轮草稿再重放 | 否则回放 `delta` 会叠在草稿上，出现重复 |
| `components/ChatWindow.tsx` | 错误按钮固定“重试” | `interrupted` 时用 warning + “继续” | 断流不是普通错误，用户需要明确的继续动作 |

### 验证

```bash
PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run typecheck
# 通过

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run test
# 8 files, 54 tests passed

PATH=/Users/lidong/.nvm/versions/node/v20.20.2/bin:$PATH npm --prefix client run build
# vite v5.4.21；dist JS 807.66 kB（gzip 254.55 kB）
```

### 关键行为

- 正常流：收到 `done` → 不触发对账。
- 流提前结束 / 网络断开：
  1. 保留本地已收到的消息与进度；
  2. `GET /api/conversations/{id}/messages` 对账；
  3. 有 `reply_to_client_message_id === client_message_id` 的助手消息 → 用服务端最终内容、动作、金额替换本地草稿；
  4. 没有 → 显示“连接中断，请点击继续”，保留本地内容；
  5. 历史接口失败 → 显示“历史恢复失败，请重试”，本地内容不丢；
  6. 历史接口 404（会话已删/不属于本人）→ 清空并提示重新开始。
- 用户点“继续”才重试；复用同一个 `client_message_id` + `conversation_id`，不自动无限重试。
- 429 `busy` → 冷却 5 秒并提示，不自动重试。
- 用户停止 → `AbortController.abort()`；状态标记 `interrupted`，允许同一个 id 继续。

### 边界

- `ack` 丢失时前端没有 `conversation_id`，重试会新建会话；单会话 UI 不展示孤儿会话，跨会话去重不在 Ch08 范围（见 spec §7.4 / TODO P3 #8）。
- 原请求仍在跑时重试会收到 429 `busy`；提示“还在处理”，不自动重试。
- 本 task 不做 token 级断点续生成，也不做服务端片段 buffer。

---

## 11.1 · App 逻辑抽离（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/hooks/useChatSession.ts` | 聊天会话状态与副作用：SSE 发送、断流对账、停止/重试/动作、派生 `visibleActions/showSide` |
| `client/src/hooks/useConversations.ts` | 会话列表、启动恢复、切换会话、历史加载 |
| `client/src/lib/storage.ts` | `getStorage` / `rememberClientMessageId` / `rememberConversationId` / `forgetConversationId` |
| `client/src/lib/actionLabel.ts` | 动作卡片 → 回填文本 |

### 修改与原因

| 文件 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `App.tsx` | 449 行：状态、bootstrap、SSE、断流、动作、存储、JSX 全在一个组件 | 103 行：只保留布局，调 `useChatSession` | 逻辑和视图耦合，改一处要读整文件 |
| `hooks/useChatSession.ts` | — | 组合 `useConversations` + 聊天执行逻辑 | 会话列表和单轮发送是两块独立职责 |
| `hooks/useConversations.ts` | — | 列表/历史/bootstrap | 切换会话不应和 SSE 发送混在一起 |
| `lib/storage.ts` | `App.tsx` 底部三个存储函数 | 独立模块 | localStorage/URL 细节不该留在页面组件 |
| `lib/actionLabel.ts` | `App.tsx` 底部 `actionLabel` | 独立纯函数 | 可单测、可复用 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 8 files, 54 tests passed
npm run build       # 通过；dist JS 808.27 kB（gzip 254.74 kB）
```

### 边界

- 纯重构，行为不变；Task 11 的断流/重试逻辑原样搬到 hook。
- 没有引入状态管理库；`useReducer + hooks` 足够当前规模。
- App 现在只负责布局；后续加页面时不需要再改聊天执行逻辑。

---

## 12 · 四场景验收与交付文档（2026-11-16）

### 新增

| 文件 | 内容 |
|---|---|
| `docs/reports/ch08-acceptance.md` | 四场景 SSE 记录、重放/历史核对、回归结果、已知边界 |
| `README.md` | 重写快速开始：建库、迁移、后端 8090、前端 5190、回归命令 |
| `tests/core/test_progress.py` | 补 `validate` / `query_data` 的 `checking_order` 映射 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/core/progress.py › node_frame` | 只在 `query_data` 的 update 有 `order_snapshot` 时发 `checking_order` | `validate` / `query_data` 都看“本节点 update + 已累积 state” | 真实规则图在 `validate` 查订单，live 输出漏了“核对中” |
| `docs/reports/todo.md` | P0 仍写“当前阻塞” | P0 标为已关闭；P3 #2/#7 关闭；保留 P3 #8 | 验收后状态要能一眼看出 |
| `docs/reports/00-change-log-overview.md` | Ch08 🚧 Task 11 | Ch08 ✅（截图/录屏待补） | 章节状态与验收一致 |
| `docs/reports/README.md` | 下一步 Ch08 | Ch08 Task 12 验收完成；截图/录屏待补 | 索引页不能停留在旧状态 |
| `docs/superpowers/plans/ch08-frontend.md` | 场景 2/4 样例与实际路由不符 | 场景 2 改用投诉样本；场景 4 补齐缺失槽位 | 验收样例必须能真实复现 |

### 验证

```bash
uv run pytest -q
# 1073 passed, 10 skipped

uv run ruff check .
# All checks passed!

cd client
npm run typecheck   # 通过
npm test            # 8 files, 54 tests passed
npm run build       # 通过；dist JS 808.27 kB（gzip 254.74 kB）
```

### 四场景结果

1. `订单1002 不想要了` → `rules`；`checking_order` → `amount_ready(124900)` →
   `waiting_confirm`；金额只出现一次。
2. `我要投诉你们的客服` → `human`；`handoff`；`actions=[transfer_human]`；不建工单。
3. `退货政策是什么` → `knowledge`；`retrieving` → `evidence_ready` → `answering`；
   引用含 `faq#退货政策是什么`。
4. `我要退款` → `退货政策是什么` → `继续退费` → `1002` → `不想要了`：
   park 提示 + `resume_flow`；恢复后补齐缺失槽位，最终完成退款。

### 断流结果

- 同一 `client_message_id` 重放：conv 23 仍是 1 条 user + 1 条 assistant；
- 回放 `delta` 与首次一致；
- 历史接口返回 `reply_to_client_message_id`、`meta.actions`、`meta.signals.amount`；
- 浏览器 DevTools Offline 演练与截图/录屏待人工补做。

### 边界

- `ack` 丢失时继续会新建会话（P3 #8 保留）。
- 没有 token 级流式、没有工单闭环、没有真实坐席队列。
- 构建体积仍超过 500 kB，P9 交付前再评估。

---

## 12.1 · 视觉与布局调整（2026-11-16）

### 修改与原因

| 文件 | 旧布局 | 新布局 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/App.tsx` | 三栏 + 输入框横跨整页 | 左会话栏 / 中聊天+输入 / 右进度动作；品牌头部 + 在线状态 | 输入框和聊天区不对齐，页面重心散 |
| `client/src/styles.css` | 纯灰底、细边框卡片、间距偏紧 | 渐变背景、玻璃感卡片、圆角/阴影、欢迎页、响应式断点 | 视觉太素，缺少层次 |
| `client/src/components/ChatWindow.tsx` | 只有消息列表 | 聊天头部 + 欢迎页 + 三个快捷问题 | 空会话没有引导，顶部没有“正在和谁聊” |
| `client/src/components/Composer.tsx` | 普通 TextArea + 按钮 | 圆角输入卡、底部提示、发送/停止图标按钮 | 输入区像表单，不像聊天 |
| `client/src/components/ConversationList.tsx` | 纯文字列表 | 图标标题 + 胶囊“新对话” + 选中态强调 | 左侧层级弱，当前会话不明显 |
| `ProgressPanel` / `ActionBar` | 只有标题 | 标题加图标/状态 | 侧栏卡片辨识度低 |
| `ProgressPanel` 标题图标 | 无条件 `LoadingOutlined` | `inFlight` 时转圈，空闲时 `CheckCircleOutlined` | antd 的 loading 图标默认自带旋转，空闲时也会一直转 |
| `App.tsx` 顶部状态 | 展示内部路由「知识问答」+ 静态「在线」 | 移除；C 端不暴露 A/B/C/D/E 出口 | 路由是内部实现细节，不是用户价值；「在线」也没有真实 presence |
| `ChatWindow` 副标题 | 「在线 · 通常几秒内回复」 | 「智能客服 · 通常几秒内回复」 | 避免暗示真人坐席在线 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 8 files, 54 tests passed
npm run build       # 通过；dist JS 813.49 kB（gzip 256.93 kB），CSS 12.17 kB
```

### 边界

- 纯视觉/布局调整，不改聊天、断流、动作逻辑。
- 空会话快捷问题复用 `handleSend`，仍走同一套 SSE/幂等逻辑。
- 构建体积仍超过 500 kB；P9 交付前再评估 code split。

---

## 12.2 · 身份问答修正（2026-11-16）

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/graph/nodes.py › fallback_exit` | `你是谁` 不在 `_SOCIAL_WORDS`，也不含业务词 → `UNKNOWN_REPLY` + `transfer_human` | 新增 `_SELF_INTRO_WORDS` 与 `SELF_INTRO_REPLY`，回答身份/能力，不再推转人工 | C 端问“你是谁”是基础问题，答“没理解”并推人工是产品错误 |
| `tests/graph/test_human_fallback.py` | 只覆盖寒暄/致谢/未知 | 补 6 条身份/能力用例 | 防止以后关键词调整把身份问题又打回兜底 |

### 验证

```bash
uv run pytest tests/graph/test_human_fallback.py -q
# 27 passed

uv run ruff check app/graph/nodes.py tests/graph/test_human_fallback.py
# All checks passed!
```

实际 SSE：

```text
route: fallback / 闲聊
progress: working done
delta: 我是电商售后智能客服，可以帮您查订单、算退款，也能解释退货、物流和优惠券规则……
done
```

### 边界

- 仍是关键词脚本，不调模型；`你们老板是谁` 这类问题不会被误判成身份介绍。
- 身份回答不带 `transfer_human`，用户不会被无必要地推向人工。

---

## 12.3 · 聊天自动滚动（2026-11-16）

### 修改与原因

| 文件 | 旧行为 | 新行为 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/components/ChatWindow.tsx` | 消息列表固定在顶部，新回复在视口外 | 监听 `messages/inFlight`，自动滚到最新；用户上滑阅读时不强拉 | 回复较长时用户必须手动滑到底，容易以为“没回复” |
| `client/src/styles.css` | 无平滑滚动 | `scroll-behavior: smooth` | 大段回答直接跳到底太生硬 |

### 规则

- 用户刚发出消息：强制回到底部；
- 用户停留在底部附近（<80px）：新 `delta` / `done` / 加载提示出现时自动跟随；
- 用户主动上滑阅读历史：不强行打断，避免“看一半被拽走”。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 8 files, 54 tests passed
npm run build       # 通过；dist JS 814.08 kB（gzip 257.12 kB）
```

---

## 12.4 · 文字「转人工」短路（2026-11-16）

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/api/chat.py › run_turn` | 只有 `selected_action.type=transfer_human` 才走 D 出口 | 增加 `_is_transfer_request(text)`：用户直接打「转人工 / 找人工 / 人工客服」也走同一短路 | 用户在输入框里打「转人工」会被当普通文本分类，落到 `UNKNOWN_REPLY` |
| `tests/api/test_chat_sse.py` | 只测结构化转人工 | 补 3 条文字转人工：不跑图、route=human、无工单 | 防止以后改关键词把文字入口打回兜底 |

### 验证

```bash
uv run pytest tests/api/test_chat_sse.py -q
# 44 passed

uv run pytest -q
# 1073 passed, 10 skipped

uv run ruff check .
# All checks passed!
```

实际 SSE：

```text
route: human / 其他
progress: handoff waiting
delta: 已为您转接人工客服，请稍候……
actions: [{"type":"transfer_human"}]
done
```

### 规则

- 命中「转人工 / 转接人工 / 找人工 / 要人工 / 人工客服 / 人工服务 / 转客服 / 找客服 / 要客服」；
- 单独输入「人工」也命中；
- 「你们老板是谁」不会命中，仍走未知兜底。

---

## 12.5 · 转人工按钮去重（2026-11-16）

### 修改与原因

| 文件 | 旧行为 | 新行为 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/state/visibleActions.ts` | 只在最后一条用户消息是「转人工」时隐藏按钮 | `route=human` 时也隐藏 | 投诉/兜底回复本身已经执行了转人工，再给按钮会重复转接 |
| `client/src/hooks/useChatSession.ts` | 组件内联过滤 | 调纯函数 `visibleActionsFor` | 规则可单测，避免回归 |
| `client/src/state/visibleActions.test.ts` | 无 | 3 条：转人工前显示、已转人工隐藏、用户已输入隐藏 | 防止“转人工按钮反复出现” |

### 场景

1. 输入「我要投诉」→ `route=human`，回复“已为您转接人工客服”→ **右侧不再出现转人工按钮**；
2. 未知问题 → `route=fallback`，仍显示转人工按钮；
3. 点按钮 → `route=human`，按钮消失；
4. 用户自己发「转人工」→ 按钮也不重复出现。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 9 files, 57 tests passed
npm run build       # 通过；dist JS 814.13 kB（gzip 257.13 kB）
```

---

## 12.6 · 转人工进度文案（2026-11-16）

### 修改与原因

| 文件 | 旧文案 | 新文案 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/components/ProgressPanel.tsx` | 「正在转接人工」 | 「已转接人工，等待接入」 | 回复已经说“已为您转接”，进度还写“正在转接”，状态前后矛盾 |
| `app/core/progress.py › STAGE_LABELS` | 「正在转接人工」 | 「已转接人工，等待接入」 | 文档/文案参考与前端保持一致 |
| `client/src/components/progress.test.tsx`、`tests/core/test_progress.py` | 旧文案 | 新文案 | 防止回退 |

### 语义

- `stage=handoff`、`status=waiting` 没变；
- 变的是用户看到的文案：**转接动作已完成，当前在等人工接入**。

### 验证

```bash
uv run pytest tests/core/test_progress.py -q   # 7 passed
uv run pytest -q                               # 1073 passed, 10 skipped
uv run ruff check .                            # 通过

cd client
npm run typecheck                              # 通过
npm test                                       # 9 files, 57 tests passed
npm run build                                  # 通过
```

---

## 12.7 · 转人工终态提示（2026-11-16）

### 问题

`route=human` 之后，进度卡一直显示「已转接人工，等待接入」，看起来像还在转圈；
但 Ch08 没有真实坐席队列，也没有超时状态，这个提示会一直停留到用户发下一条消息或新开对话。

### 修改

| 文件 | 旧行为 | 新行为 | 原因 |
|---|---|---|---|
| `ProgressPanel.tsx` | `route=human` 用 `process` 状态 | 改成 `finish`，标题「已转接人工」 | 转接动作已完成，不是仍在执行 |
| `ProgressPanel.tsx` | 无说明 | 增加「转接请求已提交，等待人工接入期间可以继续描述问题」 | 明确不是卡死，用户仍可继续输入 |
| `progress.test.tsx` | 无 human 终态用例 | 补 1 条 | 防止回退成一直转圈 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 9 files, 58 tests passed
npm run build       # 通过；dist JS 814.24 kB（gzip 257.23 kB）
```

---

## 12.8 · 区分“已转接”和“转人工按钮”（2026-11-16）

### 问题

用户先在上一轮转人工，随后输入 `******`：

- 回复走 fallback：「没理解…点转人工」；
- 右侧又出现「转人工」；
- 进度却显示「已转接人工，等待接入」。

原因是后端 `final_frame` 看到 `actions` 里有 `transfer_human` 就当“已转接”，
但 fallback 里的动作只是**提供一个按钮**，用户还没点。

### 修改

| 文件 | 旧逻辑 | 新逻辑 |
|---|---|---|
| `app/core/progress.py › final_frame` | 有 transfer 动作 = handoff | 新增 `actual_handoff`；只有 route=human 或真实 transfer_reason 才算 |
| `app/api/chat.py` | 不区分 | `actual_handoff = route == "human" or bool(final.transfer_reason)`；写进 `signals.handoff` |
| `app/api/conversations.py` | 不返回 handoff 信号 | 白名单增加 `handoff` |
| `client/src/state/turn.ts` | 无全局转接标记 | `handoffStarted`：route=human / rules transfer 后置 true |
| `client/src/state/reconnect.ts` | 只看最后一条 | 从整段历史推断 handoffStarted；fallback 的按钮不算 |
| `client/src/state/visibleActions.ts` | 只看当前 route | `handoffStarted=true` 时隐藏后续转人工按钮 |

### 效果

- `******`：回复仍是“没理解 + 可点转人工”，这是对的；
- 如果之前已经转人工：右侧不再重复出现按钮；
- 进度只显示「已处理」，不会假装“已转接”；
- 真正投诉/文字转人工：progress=handoff，handoffStarted=true。

### 验证

```bash
uv run pytest -q          # 1074 passed, 10 skipped
uv run ruff check .       # 通过
cd client
npm run typecheck         # 通过
npm test                  # 9 files, 60 tests passed
npm run build             # 通过
```

---

## 12.9 · 时间显示修正 + 聊天气泡时间（2026-11-16）

### 问题

后端时间按 UTC 存储，但 JSON 返回的是无时区后缀的 naive 字符串：

```text
2026-11-16T13:06:33
```

浏览器 `new Date(...)` 会把它当本地时间，东八区直接少 8 小时。
所以左侧会话列表显示 `13:06`，实际本地时间是 `21:06`。

### 修改

| 文件 | 内容 |
|---|---|
| `client/src/lib/time.ts` | `parseServerTime`：无时区时补 `Z`；`formatConversationTime` / `formatMessageTime` |
| `ConversationList.tsx` | 用 `formatConversationTime`；当天显示「今天 HH:mm」，昨天显示「昨天 HH:mm」 |
| `MessageBubble.tsx` | 气泡角色旁增加 `HH:mm` 时间 |
| `state/turn.ts` | `ChatMessage.createdAt`；新消息写入本地时间 |
| `state/reconnect.ts` | 历史消息透传 `created_at` |
| `lib/time.test.ts` | 3 条：naive UTC、显式时区、无效值 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 63 tests passed
npm run build       # 通过；dist JS 815.56 kB（gzip 257.57 kB）
```

### 边界

- 按浏览器本地时区展示；服务端统一 UTC。
- 实时消息时间取浏览器当前时间；刷新后以服务端 `created_at` 为准。

---

## 12.10 · 退款原因改为选项 + 其他原因输入（2026-11-16）

### 问题

退款流程追问原因时只给一句文本，用户需要自己打字；原因码是固定三类
（质量问题 / 不想要了 / 其他），完全可以做成按钮。

### 修改

| 文件 | 内容 |
|---|---|
| `app/graph/rules_exit.py › _ask_reason` | 返回 3 个 `select_reason` 动作：quality / no_reason / other（other 带 `needs_text`） |
| `app/core/slot_fill.py` | `select_reason` → `reason_code`；other 必须带 `reason_text` |
| `client/src/components/ActionBar.tsx` | 渲染原因按钮；“其他原因”打开 Modal + TextArea，提交后带 `reason_text` |
| `client/src/lib/actionLabel.ts` | 原因动作映射成用户消息文本 |
| `tests/core/test_slot_fill.py`、`tests/graph/rules/test_intake.py`、`client/src/components/actions.test.tsx` | 覆盖固定原因、其他原因、无文本拒绝、动作列表 |

### 实际流程

```text
请提供订单号 → 选择订单
→ 请问退货原因（质量 / 不想要了 / 其他）
→ 选「商品有质量问题」→ 直接继续
→ 选「其他原因」→ 弹窗输入 → 提交后继续
```

### 验证

```bash
uv run pytest -q          # 1076 passed, 10 skipped
uv run ruff check .       # 通过

cd client
npm run typecheck         # 通过
npm test                  # 10 files, 65 tests passed
npm run build             # 通过；dist JS 845.30 kB（gzip 266.71 kB）
```

### 边界

- `other` 的补充说明进入 `refund_draft.reason_text`，供判定 prompt 参考；
- 但判定仍按固定 `reason_code` 三分类走，不新增枚举；
- 其他原因的文本框最多 200 字。

---

## 12.11 · 「其他原因」弹窗计数遮挡修复（2026-11-16）

### 问题

antd `Input.TextArea` 的 `showCount` 默认把计数绝对定位在输入框下方，
正好压到 Modal footer 的「提交」按钮上，出现 `0 / 200` 和按钮重叠。

### 修改

```css
.reason-modal .ant-input-textarea-show-count .ant-input-data-count {
  position: static;
  display: block;
  margin-top: 4px;
  text-align: right;
}
```

计数改为正常文档流，放在输入框下方、footer 上方，不再遮挡按钮。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 65 tests passed
npm run build       # 通过；dist JS 845.33 kB（gzip 266.72 kB）
```

---

## 12.12 · 选项卡移入聊天流（2026-11-16）

### 问题

右侧「下一步」卡片和聊天内容分离：用户看到助手问“请选择订单/原因”，
选项却在右侧栏，视线要来回跳；历史消息多时更容易错位。

### 修改

| 文件 | 旧行为 | 新行为 |
|---|---|---|
| `ChatWindow.tsx` | 只渲染消息 | 增加 `actions` / `onAction`，动作直接渲染在最后一条消息下方 |
| `ActionBar.tsx` | 固定 Card + 「下一步」标题 | 新增 `embedded` 模式：只渲染按钮，不要 Card 外壳 |
| `App.tsx` | 右侧栏同时放进度 + 动作 | 右侧栏只放进度；动作移入聊天流 |
| `styles.css` | 无内联动作样式 | `.action-inline` 左对齐，和助手气泡对齐 |

### 效果

```text
客服：请选择订单
[CS-04 星光灰 · 1899.00 元]
[ST-20 石墨灰 · 1249.00 元]
[FB-10 奶油白 · 599.00 元]
```

选项紧跟在问题下面，右侧只保留「处理进度」。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 66 tests passed
npm run build       # 通过；dist JS 845.52 kB（gzip 266.78 kB）
```

---

## 12.13 · 内联选项改为纵向卡片（2026-11-16）

### 问题

选项虽然移进了聊天流，但三个订单按钮横排：
- 一屏放不下，第三张被裁切；
- 只有 `标题 · 金额`，没有订单状态，像普通按钮；
- 和助手气泡的层级不够清楚。

### 修改

| 文件 | 旧样式 | 新样式 |
|---|---|---|
| `ActionBar.tsx` | 内嵌时 `Space wrap` 横排 | `Space direction="vertical"` 纵向列表 |
| `ActionBar.tsx` | 订单按钮 `title · amount` 一行 | 两行：标题 + `状态 · 金额` |
| `styles.css` | `.action-inline` 只做左对齐 | 最大宽度 420px、按钮全宽、纵向卡片、其他原因虚线 |

### 效果

```text
客服：没有找到这笔订单，您可以选一笔最近的订单：
┌──────────────────────────────┐
│ CS-04 星光灰                 │
│ 待发货 · 1899.00 元          │
└──────────────────────────────┘
┌──────────────────────────────┐
│ ST-20 石墨灰                 │
│ 已发货 · 1249.00 元          │
└──────────────────────────────┘
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 66 tests passed
npm run build       # 通过；dist JS 845.88 kB（gzip 266.87 kB）
```

---

## 12.14 · 订单选项卡片视觉增强（2026-11-16）

### 问题

纵向卡片虽然不裁切了，但仍像普通白底按钮：
- 只有标题 + 状态/金额，层级弱；
- 缺少“可选”暗示，没有 hover/箭头；
- 卡片面积大、留白多。

### 修改

| 文件 | 新样式 |
|---|---|
| `ActionBar.tsx` | 订单卡：左侧购物图标 + 标题 + 状态 Tag/金额 + 右侧箭头；图标 aria-hidden |
| `styles.css` | 卡片 hover 左移 2px、蓝色描边、阴影；`option-icon/copy/meta/arrow` 分层 |
| `actions.test.tsx` | 适配可访问名称（装饰图标不参与） |

### 效果

```text
[🛍] CS-04 星光灰            ›
     待发货 · 1899.00 元

[🛍] ST-20 石墨灰            ›
     已发货 · 1249.00 元
```

原因选项保持紧凑卡片，右侧箭头提示可点。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 66 tests passed
npm run build       # 通过；dist JS 847.07 kB（gzip 267.13 kB）
```

---

## 12.15 · 选项卡片跟随产生它的助手消息（2026-11-16）

### 问题

选项虽然移进了聊天流，但渲染在**整个消息列表末尾**：
- 用户点完「选择订单 1003」后，新用户气泡插在选项上方；
- 卡片看起来像是挂在这条用户消息下面，位置不对；
- 用户已经回答后，旧选项还留着。

### 修改

`ChatWindow.tsx`：

- 计算 `lastAssistantIndex`；
- 只在**最后一条消息是 assistant** 时展示该轮的 actions；
- 选项渲染在对应 assistant 气泡之后，而不是整个列表末尾；
- 用户一旦发出新消息（最后一条是 user），旧选项立即消失。

### 效果

```text
客服：请选择订单
[订单卡片 A]
[订单卡片 B]
[订单卡片 C]
用户：选择订单 1003
（旧卡片消失）
客服：好的，请补充退款原因…
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 67 tests passed
npm run build       # 通过；dist JS 847.24 kB（gzip 267.19 kB）
```

---

## 12.16 · 「其他原因」数字被当成订单号（2026-11-16）

### 复现

```text
我要退款 订单1003
→ 请选择退款原因
→ 选择「其他原因」
→ 输入 111111
→ 错误：没有找到这笔订单，您可以选一笔最近的订单
```

### 原因

恢复轮里 `slot_fill` 已经填好 `reason_code=other`，
但图恢复后 `intake` 仍从 `raw_query="111111"` 里抽订单号，
把上一轮选好的 `order_id=1003` 覆盖成了 `111111`。

`reason_text` 里的数字被误当成订单号。

### 修复

`app/graph/rules_exit.py › intake`：

```python
existing = state.get("order_id")
if existing and existing != order_id:
    # 恢复轮补充的是原因/其他槽位，这句话里的数字不能覆盖已选订单
    return {}
```

效果：

- 已有订单 1003 时，输入「111111」不会被当成新订单；
- 原因继续记为 `other + reason_text=111111`；
- 本轮正常算金额：`1899.00 元`。

### 验证

```bash
uv run pytest -q          # 1077 passed, 10 skipped
uv run ruff check .       # 通过
cd client
npm test                  # 10 files, 67 tests passed
```

---

## 12.17 · 删除会话（软删除）（2026-11-16）

### 结论

需要，但做**软删除**：只写 `conversations.deleted_at`，消息保留用于审计/回放；
列表、历史、后续请求统一按“不存在”处理。

### 修改

| 文件 | 内容 |
|---|---|
| `app/db/repo_conversation.py` | `soft_delete_conversation`：归属校验 + `deleted_at=now` |
| `app/api/conversations.py` | `DELETE /api/conversations/{cid}`，204；不存在/他人/已删统一 404 |
| `client/src/lib/api.ts` | `deleteConversation(cid)` |
| `client/src/hooks/useConversations.ts` | `removeConversation`：成功后从列表移除 |
| `client/src/hooks/useChatSession.ts` | `handleDeleteConversation`：删除当前会话时 reset + 清 URL/localStorage |
| `client/src/components/ConversationList.tsx` | hover 显示删除按钮，`Popconfirm` 二次确认 |
| `tests/api/test_conversations_api.py`、`conversations.test.tsx` | 覆盖 204、404、二次确认 |

### 交互

- 鼠标移到会话上 → 右侧出现垃圾桶；
- 点删除 → 气泡确认“删除这个会话？”；
- 确认后从列表消失；
- 如果删的是当前会话 → 清空聊天区并回到新对话；
- 数据库仍保留该会话的消息。

### 验证

```bash
uv run pytest -q          # 1079 passed, 10 skipped
uv run ruff check .       # 通过
cd client
npm run typecheck         # 通过
npm test                  # 10 files, 68 tests passed
npm run build             # 通过；dist JS 852.76 kB（gzip 268.74 kB）
```

实际接口：删除返回 204，列表立即不再包含该会话。

---

## 12.18 · 多意图先澄清（最小版）（2026-11-16）

### 问题

输入：

```text
我要退款退货，另外还要查一下物流
```

旧行为只按主意图走退款，直接问「哪一单」，物流诉求被静默忽略。
用户不知道系统到底在处理哪件事。

### 原因

`classify_intent` 只把 `multi_intent` 当返回值，没有写进 state；
就算写了，`route_by_intent` 仍按 `intent` 路由到 `rules`；
到了 fallback 子图，`物流` 关键词又会触发 ReAct，只答一半。

### 修改

| 文件 | 修改 |
|---|---|
| `app/graph/state.py` | 增加 `multi_intent: bool` |
| `app/graph/nodes.py` | `classify_intent` 写 `multi_intent`；为 true 时 route→fallback |
| `app/graph/nodes.py › fallback_exit` | 多意图优先返回澄清话术，不推转人工 |
| `app/graph/routing.py › route_by_intent` | `multi_intent` 时强制 fallback，避免主意图抢跑 |
| `app/graph/fallback_agent.py › after_gate` | 多意图绕过 ReAct，直接走 scripted/澄清 |
| `tests/graph/*` | 补 4 条：节点路由、澄清话术、路由函数、绕过 ReAct |

### 实际效果

```text
route: fallback / 退款申请
progress: working
delta: 您这句话里提到了不止一件事，想先处理哪一件？
       可以告诉我是退款、查订单/物流，还是政策问题。
```

### 边界

- 这是最小版：先反问，不记录 `pending_question`；下一轮用户说「先查物流」会重新分类。
- 未实现“把两个意图都列出来并记住选择”；P5-11 标为 ⚠️ 而不是 ✅。

### 验证

```bash
uv run pytest -q          # 1083 passed, 10 skipped
uv run ruff check .       # 通过
cd client
npm test                  # 10 files, 68 tests passed
```

---

## 12.19 · 处理进度移入聊天流（2026-11-16）

### 问题

右侧独立的「处理进度」卡片与聊天内容割裂：
- 用户在主区域看回答，进度在右侧；
- 对话变长后视线要来回跳；
- 窄屏下右侧栏还会挤压聊天区。

### 修改

| 文件 | 修改 |
|---|---|
| `ProgressPanel.tsx` | 新增 `embedded` 模式：紧凑横排步骤，无 Card 外壳 |
| `ChatWindow.tsx` | 接收 `progress` 节点；完成态挂在最后一条 assistant 下方，进行中挂在列表末尾 |
| `App.tsx` | 删除右侧栏，进度作为 `progress` 传给聊天窗 |
| `useChatSession.ts` | 删除 `showSide` |
| `styles.css` | 新增 `.progress-inline` 紧凑样式；聊天列最大宽度 1040px 居中 |

### 效果

```text
客服：支持七日无理由退货……
处理进度  已检索知识库 → 已找到依据 → 已整理回答
[相关引用]
```

进行中：

```text
正在处理…
处理进度  已检索知识库 → 正在检查依据
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 10 files, 69 tests passed
npm run build       # 通过；dist JS 853.95 kB（gzip 269.09 kB）
```

---

## 12.20 · 引用序号可点开看来源（2026-11-16）

### 问题

回答里的 `[1] [2] [3]` 原来只有 `title` tooltip：
- 桌面要悬停才出现；
- 移动端没有 hover；
- 只显示标题，看不到依据原文。

### 修改

| 层 | 修改 |
|---|---|
| `app/api/chat.py` | `citations` 帧增加 `details`（id / section_path / text / source）；meta 持久化 `citations` |
| `app/api/conversations.py` | 历史 meta 白名单增加 `citations` |
| `client/src/state/turn.ts` | `CitationDetail`；citations 帧解析 details |
| `client/src/state/reconnect.ts` | 历史恢复 citations |
| `client/src/components/MessageBubble.tsx` | 序号改为 antd `Popover`，hover/click 打开；显示原文片段与来源文件 |
| `styles.css` | 引用序号下划线、Popover 正文滚动 |

### 效果

```text
支持七日无理由退货……[1]
                           ↑ 点击
┌────────────────────────────────────┐
│ 退货政策是什么                     │
│ 支持七日无理由退货：自签收次日起…  │
│ 来源：faq.md                       │
└────────────────────────────────────┘
```

### 验证

```bash
uv run pytest -q          # 1084 passed, 10 skipped
uv run ruff check .       # 通过
cd client
npm run typecheck         # 通过
npm test                  # 10 files, 70 tests passed
npm run build             # 通过；dist JS 854.61 kB（gzip 269.32 kB）
```

---

## 12.21 · 引用弹层尺寸与排版修正（2026-11-16）

### 问题

点开引用后，Popover 直接铺满整宽：
- 长文本没有最大宽度；
- `**` 和 `-` 原样展示，像原始 Markdown；
- 换行被折叠成一大段；
- 弹层覆盖整个聊天区。

### 修改

| 文件 | 修改 |
|---|---|
| `MessageBubble.tsx` | Popover `styles.body` 限制 `maxWidth:420` / `maxHeight:320`；`formatCitationText` 清理 `**`、转换列表符、去掉重复标题 |
| `styles.css` | `.citation-popover` 最大宽 `min(380px,78vw)`、最大高 260px、`white-space: pre-wrap` |

### 效果

```text
[1] 点击
┌──────────────────────────────┐
│ 退货政策是什么               │
│ 支持七日无理由退货：……       │
│ • 定作/定制商品不适用         │
│ • 鲜活易腐商品不适用          │
│ 来源：faq.md                 │
└──────────────────────────────┘
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 70 tests passed
npm run build       # 通过；dist JS 854.84 kB（gzip 269.41 kB）
```

---

## 12.22 · 去掉冗余的内联进度（2026-11-16）

### 问题

- 追问订单号时，助手问题下面还挂「处理进度 · 等待您补充信息」；
- 发「物流查询」时，同时出现「处理进度 · 正在理解」和「正在处理…」；
- 状态重复，打断“问题 → 选项”的阅读顺序。

### 修改

`ProgressPanel` 内联模式过滤掉不提供信息的步骤：

- `waiting_info`：由助手问题和选项表达，整条进度隐藏；
- `understanding` / `working`：与打字提示重复，隐藏；
- 只有真正的处理步骤（核对订单 / 金额 / 检索 / 证据 / 回答 / 转接）才展示。

### 效果

- 问“哪一单”：直接是问题 + 订单卡片，不再夹一条“等待补充信息”；
- 发“物流查询”：只显示「正在处理…」，不再出现「正在理解」；
- 退款算出金额：仍显示「已核对订单 → 已算出金额 → 等待确认」。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 71 tests passed
npm run build       # 通过；dist JS 854.96 kB（gzip 269.45 kB）
```

---

## 12.23 · 继续按钮紧跟提示，进度放到动作之后（2026-11-16）

### 问题

助手回答末尾写着：

> （您还有一笔未完成的数据查询，需要继续吗？）

但「继续查询」按钮被「处理进度」隔开，出现在更下面；
而且单按钮被拉成整行宽，像一张大横幅。

### 修改

| 文件 | 修改 |
|---|---|
| `ChatWindow.tsx` | 渲染顺序改为：助手消息 → actions → progress |
| `styles.css` | 去掉 `.action-inline .ant-btn` 的 `width:100%`，只让 `.option-card` 全宽 |

### 效果

```text
客服：……（您还有一笔未完成的数据查询，需要继续吗？）
[继续查询]          ← 紧跟提示，按钮自适应宽度
处理进度  已检索知识库 → 已找到依据 → 已整理回答
```

订单/原因选项仍然全宽；单个“继续/转人工”按钮不再拉满整行。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 71 tests passed
npm run build       # 通过；dist JS 854.96 kB（gzip 269.46 kB）
```

---

## 12.24 · 进度与打字提示去重（2026-11-16）

### 问题

处理中同时出现：

```text
处理进度  正在检索知识库
正在处理…
```

两个加载状态说同一件事。

### 修改

- `ProgressPanel` 导出 `hasInlineProgress(route, progress, inFlight)`；
- `App` 根据它计算 `showTyping`；
- `ChatWindow` 在 `showTyping=false` 时不再渲染「正在处理…」；
- 只有进度隐藏（如 fallback / waiting_info）时才用打字提示兜底。

### 效果

```text
处理进度  正在检索知识库
```

不再跟一条「正在处理…」。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 72 tests passed
npm run build       # 通过；dist JS 855.17 kB（gzip 269.53 kB）
```

---

## 12.25 · 处理中改为助手“正在输入”气泡（2026-11-16）

### 问题

进度胶囊单独浮在左侧，下面还有「正在处理…」：
- 两个加载状态重复；
- 胶囊没有头像/气泡，和聊天气泡体系割裂；
- 空白区域里只有一个小标签，视觉重量不足。

### 修改

| 文件 | 修改 |
|---|---|
| `ProgressPanel.tsx` | 导出 `inlineProgressLabel`：取当前 running 步骤的标题 |
| `ChatWindow.tsx` | 处理中渲染一个助手 pending 气泡：头像 + 客服 + 三个跳动点 + 当前阶段文案 |
| `App.tsx` | 传 `progressLabel`，不再传 `showTyping` |
| `styles.css` | `.pending-bubble` / `.typing-dots` / 跳动动画 |

### 效果

```text
[客] 客服  正在处理
     • • •  正在检索知识库
```

完成后再显示正式助手气泡，进度摘要留在回答下方。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 72 tests passed
npm run build       # 通过；dist JS 855.66 kB（gzip 269.60 kB）
```

---

## 12.26 · 回复中禁用会话切换（2026-11-16）

### 结论

当前是**有意禁用**：`handleSelectConversation` 在 `state.inFlight` 时直接返回。
因为前端只维护一份当前会话状态，回复中切换会让 SSE 回调写进错误会话。

### 问题

列表看起来仍可点击，点击却没反应；「新对话」按钮也没有禁用态。

### 修改

| 文件 | 修改 |
|---|---|
| `ConversationList.tsx` | 新增 `disabled`；会话项 `aria-disabled` + tooltip、点击忽略、删除/新对话按钮禁用 |
| `App.tsx` | 传 `disabled={state.inFlight}` |
| `styles.css` | 禁用项 opacity 0.55、`cursor:not-allowed`、隐藏删除按钮 |

### 现在的交互

- 回复中：左侧列表变灰，鼠标悬停提示“正在回复中，请稍候”；
- 点会话不切换；
- 「新对话」「删除」禁用；
- 回复结束后恢复。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 73 tests passed
npm run build       # 通过；dist JS 855.79 kB（gzip 269.65 kB）
```

---

## 12.27 · 回复中允许确认切换会话（2026-11-16）

### 问题

直接禁用左侧列表交互不友好：用户只想看历史，却点不动。

### 方案

不再整块禁用；点击时如果当前还在生成，弹出确认：

```text
当前回复还在生成，切换会停止本次回复。
[继续等待] [停止并继续]
```

- 取消 → 留在当前会话继续等；
- 确认 → abort 当前 SSE，切换到目标会话；
- 开始新对话同样走确认；
- 回复中删除会话 → 提示“正在回复中，请稍后再删除”。

### 实现

| 文件 | 修改 |
|---|---|
| `useChatSession.ts` | `confirmStop` + `switchingRef`；abort 时由切换动作接管状态，避免落“已停止”提示到新会话 |
| `App.tsx` | 移除列表 `disabled={state.inFlight}` |
| `ConversationList.tsx` | 保留可选 `disabled` 能力，但默认不再因回复禁用 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 73 tests passed
npm run build       # 通过；dist JS 856.09 kB（gzip 269.82 kB）
```

---

## 12.28 · 新会话在生成中切换后不丢失（2026-11-16）

### 问题

新开一个会话并发消息，AI 生成中切到别的会话后，左侧新会话消失。

### 原因

- 新会话在 `ack` 时已经落库，但列表只在 `done` 时刷新；
- 生成中切换会 abort，永远等不到 `done`；
- 切换动作只加载目标会话，没有刷新列表。

### 修改

| 时机 | 行为 |
|---|---|
| 新会话收到 `ack` | 立即 `refreshConversations()`，列表马上出现 |
| 确认切换会话 | abort 后先刷新列表，再加载目标 |
| 开始新对话（回复中确认） | reset 后刷新列表，旧会话仍可见 |

### 效果

新会话在 AI 生成中切走：

- 旧 SSE 被停止；
- 旧会话仍出现在左侧列表；
- 点回去能看到已保存的用户消息（assistant 未完成则不显示）；
- 不会“新会话凭空消失”。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 73 tests passed
npm run build       # 通过；dist JS 856.12 kB（gzip 269.84 kB）
```

---

## 12.29 · 其他原因计数改为自定义（2026-11-16）

### 问题

antd `Input.TextArea` 的 `showCount` 计数仍然叠在输入框内部右下角，
和边框/内容挤在一起；之前的 CSS 覆盖未生效。

### 修改

- 去掉 `showCount`；
- 在 TextArea 下方渲染自定义计数：

```tsx
<div className="reason-counter">{otherReason.length} / 200</div>
```

- CSS 右对齐、11px、独立一行，不再绝对定位。

### 效果

```text
┌──────────────────────────────┐
│ 例如：尺码不合适…            │
│                              │
└──────────────────────────────┘
                        0 / 200
[取消]                 [提交]
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 73 tests passed
npm run build       # 通过；dist JS 856.19 kB（gzip 269.85 kB）
```

---

## 12.30 · “其他原因”不能只填“其他”（2026-11-16）

### 问题

选退款原因「其他」，输入框里只填「其他」，系统仍按 `reason_code=other` 继续退款。
退款结论对订单 1002 本身没错（已发货未签收 → 全额退），但输入没有信息量。

### 修改

`ActionBar.tsx` 对“其他原因”增加前端校验：

```ts
const GENERIC_REASONS = new Set([
  "其他", "其他原因", "别的", "无", "没有", "不知道", "随便", "不想说",
]);
```

- 命中泛化原因 → 不提交，输入框红色错误态；
- 错误提示：「请补充具体原因，不要只填「其他」」；
- 输入变化时清除错误。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 74 tests passed
npm run build       # 通过；dist JS 856.46 kB（gzip 270.03 kB）
```

---

## 12.31 · 新会话不再重复显示客服头（2026-11-16）

### 问题

新会话的欢迎页已经写着：

```text
您好，我是售后客服
可以帮您查订单、算退款、解释售后政策。
```

上面还有一条「售后客服 / 智能客服 · 通常几秒内回复」的聊天头，信息重复。

### 修改

`ChatWindow.tsx`：`showWelcome` 为 true 时不渲染 `.chat-header`；
发出第一条消息后，欢迎页消失，聊天头正常出现。

### 效果

```text
新会话：
[欢迎图标]
您好，我是售后客服
[查退款] [问政策] [找人工]

发送后：
[客服头像] 售后客服
           智能客服 · 通常几秒内回复
[消息列表]
```

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 75 tests passed
npm run build       # 通过；dist JS 856.47 kB（gzip 270.03 kB）
```

---

## 12.32 · 小屏会话列表改为抽屉（2026-11-16）

### 问题

`<=1024px` 时三栏改成上下堆叠：
- 历史会话占满整行、固定 168px；
- 聊天区被压到下方；
- 会话多时上半屏全是列表，主对话反而很小。

### 修改

| 文件 | 修改 |
|---|---|
| `App.tsx` | `Grid.useBreakpoint()`：`lg` 以下隐藏左侧栏，头部增加「会话」按钮，打开 antd `Drawer` |
| `ConversationList` | 抽屉内复用同一组件；选中/新对话后自动关闭抽屉 |
| `styles.css` | `.mobile-history-btn`、抽屉内会话卡高度适配 |

### 小屏效果

```text
[品牌]                     [会话]
┌──────────────────────────────┐
│ 客服                         │
│ [消息]                       │
│                              │
│ [输入框]                     │
└──────────────────────────────┘
```

点「会话」→ 左侧抽屉滑出历史列表；选完关闭，聊天区保持占满屏幕。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 75 tests passed
npm run build       # 通过；dist JS 870.30 kB（gzip 274.51 kB）
```

---

## 12.33 · 刷新后恢复“继续”入口（2026-11-16）

### 问题

断流后刷新页面，历史只有 user 消息，没有 assistant：
- 页面看起来停住；
- 没有“继续/重试”按钮；
- 用户只能重新输入。

### 修改

`reconnect.ts › buildHistoryState`：

```ts
const interrupted = history.items.at(-1)?.role === "user";
```

- 最后一条是 user → 设置 `interrupted=true`；
- error 文案：「上次请求未完成，请点击继续」；
- 复用历史里的 `lastClientMessageId`，点「继续」会：
  - 后端已保存 → 回放；
  - 没保存 → 同幂等键重跑；
  - 还在跑 → 429 busy。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 76 tests passed
npm run build       # 通过；dist JS 870.40 kB（gzip 274.55 kB）
```

---

## 12.34 · 「等待用户」不再显示转圈（2026-11-17）

### 问题

`waiting_confirm` / `waiting_info` / `handoff` 的后端状态都是 `waiting`，
但 `statusOf` 把 `waiting` 和 `running` 一起折叠成了 `process`：

```ts
if (item.status === "waiting" || item.status === "running") {
  return "process";
}
```

内联进度用 `process → LoadingOutlined`，所以「等待您确认」会一直转圈，
看起来像系统还在处理，实际是在等用户操作。

### 修改

| 文件 | 修改 |
|---|---|
| `client/src/components/ProgressPanel.tsx` | `StepStatus` 增加 `waiting`；`waiting` 不再折叠成 `process`；Steps 用时钟图标覆盖 `process` 的当前态；内联进度沿用时钟 |
| `client/src/styles.css` | `.progress-inline-step.waiting` 用琥珀色，区分「等用户」和「系统运行中」 |
| `client/src/components/progress.test.tsx` | 新增 2 条测试：全量面板与内联进度都不能出现 `anticon-loading`，等待步骤要有时钟图标 |

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 11 files, 78 tests passed
npm run build       # 通过；dist JS 870.53 kB（gzip 274.55 kB）
```

---

## 12.35 · 三张交付截图（2026-11-17）

### 产出

| 文件 | 内容 |
|---|---|
| `docs/assets/ch08/01-main.png` | 新会话欢迎页、历史列表、示例问题 |
| `docs/assets/ch08/02-refund-progress.png` | 退款三态进度；等待确认用时钟，不转圈 |
| `docs/assets/ch08/03-knowledge-citation.png` | 知识问答引用 `[1]` 与来源浮层 |

### 采集方式

- Playwright + Chromium；
- 统一 1440×900 CSS 像素、2× DPR（2880×1800）；
- 分别打开 `?c=57`（退款）与 `?c=58`（知识问答）；
- 引用截图用 `hover` 打开 Popover，避免 `click` 把 `hover + click` 触发成开关两次。

### 验证

```bash
ls -lh docs/assets/ch08/
# 01-main.png 667K
# 02-refund-progress.png 713K
# 03-knowledge-citation.png 923K
```

---

## 12.36 · Ch08 面试材料（2026-11-17）

新增 [ch08-frontend.md](../interview/ch08-frontend.md)：

- 30 秒版本；
- SSE 帧 → reducer → UI 架构；
- C2-lite 断流恢复（两条路径、唯一索引、为什么不用 Last-Event-ID）；
- 保存先于展示、进度/动作、实际转人工 vs 仅提供按钮；
- 4 个 STAR 踩坑故事；
- 追问清单、不要说的话、数字速查、5 分钟演示脚本。

索引同步：`docs/interview/README.md`、`docs/reports/README.md`、
`docs/reports/resume-positioning.md`、`docs/interview/self-test.md`。
