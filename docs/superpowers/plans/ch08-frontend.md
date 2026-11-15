# Ch08 前端 实施计划

> **For agentic workers:** 按 task 顺序执行，步骤用 `- [ ]` 追踪。
> 每个 task 完成、用户确认后再 commit；不要自动 push。
> 实现与 spec 有偏差时，**同一个 task 内同步更新 spec 和本 plan**。

**Goal:** 把 Ch01–Ch07 的后端能力做成可演示的 C 端界面：
聊天窗 + SSE 帧渲染 + 进度面板 + 动作卡片 + 转人工入口 + 断流恢复；
同时补齐支撑这些能力的最小后端契约。

**Architecture:** 前端 React + Vite + TypeScript；后端沿用 FastAPI + SQLAlchemy + LangGraph。
断流恢复采用 **C2-lite**：历史对账（拉取已存消息）+ 同一 `client_message_id` 回放/重试，不做服务端片段 buffer。

**Tech Stack:** React 18+ / Vite / TypeScript / Vitest / React Testing Library；
后端不引入新运行时依赖；LangGraph 1.2.18。

**Spec:** [ch08-frontend-design.md](../specs/ch08-frontend-design.md)
**前置:** Ch01–Ch07 已验收；spec §14 的 4 个开放问题按 `docs/reports/todo.md` P0-1 的建议默认执行
（如不同意，先改 spec 再开工）；Node 20+；Chroma / Redis / Ollama 按 `docs/reports/local-env.md` 可用。

---

## 术语表（首次出现先看这里）

| 术语 | 白话解释 |
|---|---|
| **SSE** | Server-Sent Events，服务器单向推送事件流；浏览器保持一个连接，服务端一帧一帧发消息 |
| **帧（frame）** | SSE 里的一条消息；本项目有 `ack` / `route` / `progress` / `delta` / `citations` / `actions` / `done` / `error` |
| **`ack`** | 确认帧：告诉前端会话 id 和用户消息 id |
| **`route`** | 分流帧：分类完成后告诉前端走哪个出口 |
| **`delta`** | 回答帧；当前是**整段一帧**，不是逐字流 |
| **`progress`** | 进度帧（Ch08 新增）：告诉前端“哪一步完成了、算出多少钱、在等什么” |
| **幂等（idempotent）** | 同一个请求重复提交多次，效果和提交一次一样 |
| **回放（replay）** | 不重新调模型，直接把数据库里已存的回答按帧再发一遍 |
| **fail-closed** | 失败按“不通过”处理；保存失败就不展示回答，只返回错误 |
| **子图（subgraph）** | 编译后的 LangGraph 图，被当成父图的一个节点使用；`rules` / `data` / `knowledge` 都是子图 |
| **spike** | 限时技术验证：为回答一个技术问题临时写的小实验，不追求上线 |
| **span** | `trace_span` 表里的一条计时记录，用来还原一轮请求里每步花了多久 |
| **迁移（migration）** | 给已经存在的数据库表补列/补索引；`create_all` 不会做这件事 |
| **C2-lite** | 轻量断流恢复：拉历史 + 同幂等键回放/重试，不做 token 级断点续生成 |
| **P50 / P95** | 分位数：P50 是中位数；P95 表示 95% 的请求比它快，用来观察尾延迟（最慢那部分） |

---

## Global Constraints

- **不自动 commit / push**：每个 task 完成后等用户确认。
- **spec / plan 同步**：实现偏离设计时，同一个 task 内更新两份文档。
- **改动记录**：每个 task 追加 `docs/reports/ch08-frontend-changes.md`，并在 `00-change-log-overview.md` 登记。
- **`delta` 保持整段一帧**：Ch08 不做 token 级流式；进度靠 `progress` 帧，不靠打字机。
- **先落库再展示**：assistant + flow 同一事务成功后才 `yield delta`；失败 fail-closed。
- **进度只发语义 stage**：不把 LangGraph 节点名、规则 id、置信度、工具链发给前端。
- **历史接口字段白名单**：不返回 tool 消息、`trace_id`、`draft`、`order_snapshot`、`refund_judgement`。
- **前端组件不直接 `fetch`**：只能调 `lib/api.ts` / `lib/sse.ts`。
- **不建工单、不提交真实退款、不做 B 端工作台**：范围见 spec §11。
- **每个 task 的验证必须可复现**：命令写进 task，不能只写“手工看过”。

---

## Task 0: 基线核对 + `subgraphs=True` spike

**目的：** 先确认“进度面板的数据源拿得到”，再决定是否按真实进度实现。

**Files:**
- 不改产品代码。
- 结论写入：`docs/reports/ch08-frontend-changes.md`（新建，先写“Task 0”一节）。

**Step 1: 记录基线**

```bash
uv run pytest -q
ruff check .
```

记录当前数字（Ch07 结束时：**1030 passed, 16 skipped**）。
如果基线不一致，先停下来查，不要在错误基线上开发。

**Step 2: 最小图验证（已完成，可复跑）**

用同版本 LangGraph 1.2.18 搭一个父图 + 子图，分别跑：

```python
async for chunk in graph.astream(state, stream_mode="updates"):
    print(chunk)

async for chunk in graph.astream(state, stream_mode="updates", subgraphs=True):
    print(chunk)
```

确认输出形状：

```
((), {'start': {...}})                  # 顶层节点
(('rules:xxx',), {'query_data': {...}}) # 子图内部节点
((), {'rules': {...}})                  # 子图整体结束
```

**Step 3: 项目图 spike（限时 1–2 小时）**

目标不是写功能，而是回答五个问题：

- [ ] `build_graph()` 开 `subgraphs=True` 后，能拿到 `query_data` / `compliance` / `retrieve` / `generate` 的内部 update 吗？
- [ ] 内部 update 里有 `order_snapshot` / `refund_amount` / `compliance_violation` / `pending_question` 吗？
- [ ] 顶层 `rules` 的最终 update 仍然会发吗？（`final` 依赖它）
- [ ] 现有 `record_span` / 帧顺序测试受什么影响？
- [ ] 有没有明显性能下降？

建议做法：临时脚本或临时测试；用 monkeypatch 替换模型调用，避免真实花钱；
或只跑一个确定性场景（例如 `订单1002 不想要了`）。

**Step 4: 记录结论**

在 `docs/reports/ch08-frontend-changes.md` 写：

- 输出样例（截取关键 chunk）；
- 五个问题的答案；
- 决策：**真实 progress** 还是 **降级方案**。

**降级方案**（spike 不通过时）：

- 只发 `route` + 客户端计时提示；
- 最终状态 `amount_ready` / `waiting_confirm` 在 `delta` 前发；
- 同步修改 spec §6.2 / §14 和 TODO P0-1 #4。

**Commit:** 不提交 spike 临时代码；只提交结论文档（如果需要）。

---

## Task 1: 数据层：`reply_to_client_message_id` + 迁移

**目的：** 让助手回复能精确关联到触发它的用户消息，为重试回放提供字段和索引。

**Files:**
- Modify: `app/db/models.py`、`app/db/schema.sql`
- Modify: `app/db/repo_message.py`、`app/core/history.py`
- Create: `scripts/migrate_ch08.py`
- Modify: `scripts/init_db.py`（修正 `diff` 单向索引检查）
- Modify/Create: `tests/db/test_repo_message.py`、`tests/db/test_migrate_ch08.py`

**Step 1: ORM**

`app/db/models.py` 的 `Message` 增加：

```python
reply_to_client_message_id: Mapped[str | None] = mapped_column(String)
```

`__table_args__` 增加唯一部分索引：

```python
Index(
    "uk_msg_reply",
    "conversation_id",
    "reply_to_client_message_id",
    unique=True,
    sqlite_where=text("reply_to_client_message_id IS NOT NULL"),
)
```

**Step 2: schema.sql**

在 `CREATE TABLE messages` 中加列：

```sql
reply_to_client_message_id TEXT,   -- 回复的是哪个 client_message_id（K4 重试回放）
```

并加：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uk_msg_reply
  ON messages(conversation_id, reply_to_client_message_id)
  WHERE reply_to_client_message_id IS NOT NULL;
```

**Step 3: repo**

`append_message` 增加参数：

```python
reply_to_client_message_id: str | None = None
```

写入 `Message(reply_to_client_message_id=...)`。
新增查询：

```python
async def get_reply_by_client_message_id(
    cid: int, client_message_id: str, *, session: AsyncSession | None = None
) -> Message | None:
    """找‘回复这条用户消息’的助手消息。"""
```

注意：错误消息（`status="error"`）不写 `reply_to_client_message_id`。

**Step 4: history**

`save_assistant_message` 增加同名参数并透传。

**Step 5: 迁移脚本**

`scripts/migrate_ch08.py`：

```bash
uv run python scripts/migrate_ch08.py --db data/app.db
uv run python scripts/migrate_ch08.py --db data/eval.db
```

要求：

- 用标准库 `sqlite3`；
- 先 `PRAGMA table_info(messages)`，缺列才 `ALTER TABLE messages ADD COLUMN ...`；
- `CREATE UNIQUE INDEX IF NOT EXISTS ...`；
- 重复执行幂等；
- 打印“已迁移 / 已是最新”。

**Step 6: 修正 `diff`**

`scripts/init_db.py › diff()` 目前只检查 `sql_idx - orm_idx`。补：

```python
if extra := orm_idx - sql_idx:
    problems.append(f"索引只在 ORM 里: {sorted(extra)}")
```

**Step 7: 测试**

- 新列写入/读回；
- 唯一索引阻止同一 `client_message_id` 写第二条助手回复；
- 迁移脚本：旧 schema → 跑两次 → 列/索引存在；
- `tests/db/test_schema_sync.py` 仍通过。

**验证：**

```bash
uv run pytest tests/db -q
uv run python scripts/init_db.py        # 应打印 schema 一致
```

**Commit:** `feat(ch08): 助手回复关联列与幂等迁移`

---

## Task 2: 助手 meta + 先落库再展示 + fail-closed

**目的：** 保证“用户看到的回答一定已落库”，并让刷新后能恢复动作/金额/追问状态。

**Files:**
- Modify: `app/api/chat.py`
- Modify: `tests/api/test_chat_sse.py`

**Step 1: 组装 assistant meta**

在现有字段上增加：

```python
meta = {
    "intent": final.get("intent"),
    "route": final.get("route"),
    "citation_ids": citations,
    "resumed": bool(final.get("resumed")),
    "actions": actions,
    "signals": {
        "amount": amount if amount_ok else None,
        "pending_question": final.get("pending_question"),
        "refund_eligible": bool((final.get("refund_judgement") or {}).get("eligible")),
    },
}
```

`amount_ok` 的条件：

- `route == "rules"`；
- `refund_judgement.eligible` 为真；
- `refund_amount > 0`；
- `compliance_violation is None`。

不把 `order_snapshot` / 完整 `refund_judgement` / `tool_code` 写进 meta。

**Step 2: 调整顺序**

当前顺序（`app/api/chat.py:402-450`）：

```
yield delta → citations → actions → record_span → 保存 assistant + flow → yield done
```

改为：

```
输出守卫 → 算 actions / flow decision
  → 同一事务保存 assistant + flow
      ├─ 成功 → yield 最终 progress → delta → citations → actions → done
      └─ 失败 → 不 yield delta，抛 ChatError("internal_error")
```

保存时：

```python
assistant_message_id = await save_assistant_message(
    cid,
    answer,
    meta=meta,
    reply_to_client_message_id=client_message_id,
    session=s,
)
await apply_flow_decision(cid, decision, session=s)
```

**Step 3: 失败行为**

`except Exception` 中：

- `logger.exception(...)`；
- 写 `turn:persist` span，`persisted=False`；
- 抛 `ChatError("internal_error", "服务暂时不可用，请稍后重试。")`；
- 不 `yield delta` / `citations` / `actions` / `done`。

`app/main.py` 会把 `ChatError` 转成 `error` 帧。

**Step 4: span 顺序**

- `turn:persist` 的**计时**包住保存事务；
- 它的**写入**放在 `delta` 之后（成功时）或抛错前（失败时）；
- `turn:total` 放在 `delta` 之后写，避免观测写库占用首字延迟。

**Step 5: 测试**

- 保存成功：`delta` 来自 guard 后的回答；meta 含 `actions` / `signals`；
- 保存失败：没有 `delta`，只有 `error`；用户消息仍在；重试可重跑；
- `turn:persist` span 有 `duration_ms` / `persisted`；
- 原有 `test_output_guard_rewrites_before_delta_and_persist` 仍通过。

**Commit:** `feat(ch08): 先落库再展示，保存失败 fail-closed`

---

## Task 3: 按 `client_message_id` 回放

**目的：** 同一个请求重试时，不再跑图、不再保存第二条助手回复。

**Files:**
- Modify: `app/api/chat.py`
- Modify: `app/db/repo_message.py`（如 Task 1 未加查询）
- Modify: `tests/api/test_chat_sse.py`

**Step 1: 锁内检查**

在 `_run_turn_locked` 里，拿到会话、读完 flow 之后，**槽位填充之前**：

```python
if client_message_id:
    reply = await get_reply_by_client_message_id(
        cid, client_message_id, session=session
    )
    if reply is not None:
        async for frame in _replay_assistant_reply(cid, reply, client_message_id):
            yield frame
        return
```

**Step 2: 回放帧**

`_replay_assistant_reply` 先查 `client_message_id` 对应的 user 消息 id；
查不到就正常跑图（防御异常数据）。按已存内容发：

- `ack`：`conversation_id` + 已有 `user_message_id`；
- `route`：`meta.route` / `meta.intent` / `meta.resumed`；
- `progress`：由 `meta.signals` 与 `meta.actions` 还原最终状态；
- `delta`：`reply.content`；
- `citations`：`meta.citation_ids`；
- `actions`：`meta.actions`；
- `done`：`reply.id`。

**不跑图、不改 flow、不重新保存**。

**Step 3: 并发兜底**

如果保存 assistant 时撞 `uk_msg_reply`：

- 查回已写的那条；
- 用它的内容回放；
- 不抛 500。

**Step 4: 测试**

- 第一次完成 → 用同一 `client_message_id` 再发：FakeGraph 调用次数仍为 1；
- 数据库只有一条 user + 一条 assistant；
- 回放的 `delta` / `actions` / `citations` 与第一次一致；
- 第一次失败（无 assistant）→ 重试会跑图；
- 第一次还在跑 → 429 `busy` 路径不变。

**Commit:** `feat(ch08): 按 client_message_id 回放助手回复`

---

## Task 4: 会话列表 + 历史消息接口

**目的：** 刷新、换设备、断流后能恢复界面。

**Files:**
- Create: `app/api/conversations.py`
- Modify: `app/main.py`
- Modify: `app/db/repo_conversation.py`（如需要标题查询）
- Create: `tests/api/test_conversations_api.py`

**Step 1: `GET /api/conversations`**

返回：

```json
{
  "items": [
    {
      "id": 12,
      "created_at": "...",
      "updated_at": "...",
      "title": "订单1002 不想要了",
      "active_flow": "refund",
      "pending_question": "reason_code"
    }
  ]
}
```

规则：

- 当前 `demo_user_id`；
- 只返回未软删会话，`updated_at` 倒序，最多 20 条；
- `title` 取第一条非空用户消息，截断 30 字；没有则 `"新会话"`；
- 不返回 `summary` / `draft`。

**Step 2: `GET /api/conversations/{id}/messages`**

- 用 `get_conversation(cid, user_id=demo)` 做归属校验；
- 不存在 / 不属于本人 / 已软删 → **统一 404**；
- 只返回 `role in ("user", "assistant")`；
- 按 `id` 升序；
- 白名单序列化，不 dump ORM。

返回形状见 spec §6.6，关键字段：

- user：`id / role / content / created_at / client_message_id / meta.selected_action`；
- assistant：`id / role / content / created_at / status / reply_to_client_message_id / meta.{intent,route,citation_ids,resumed,actions,signals}`。

**Step 3: 注册路由**

`app/main.py`：

```python
from app.api.conversations import router as conversations_router
app.include_router(conversations_router)
```

**Step 4: 测试**

- 列表只含本人会话；排序；title；空列表；
- 消息接口：本人 200；不存在/他人/软删统一 404；
- tool 消息不返回；
- meta 白名单：内部字段不出现；
- 错误助手消息正常返回，且没有 `reply_to_client_message_id`。

**Commit:** `feat(ch08): 会话列表与历史消息接口`

---

## Task 5: `progress` 帧（子图事件）

**目的：** 在等待期间给前端真实里程碑，而不是定时器假进度。

**Files:**
- Create: `app/core/progress.py`
- Modify: `app/api/chat.py`
- Create: `tests/core/test_progress.py`
- Modify: `tests/api/test_chat_sse.py`

**Step 1: 定义 stage 映射**

`app/core/progress.py`：

```python
STAGES = {
    # rules
    "query_data": "checking_order",
    "compliance": "amount_ready",
    "need_info": "waiting_info",
    # data
    "query": "querying_data",
    "format": "data_ready",
    # knowledge
    "retrieve": "retrieving",
    "judge_evidence": "evidence_ready",
    "generate": "answering",
    # fallback
    "gate": "working",
    "agent": "working",
    "scripted": "working",
    # human
    "human": "handoff",
}
```

对外只发 stage 字符串；不把节点名发出去。

**Step 2: 解析 chunk**

`astream(..., stream_mode="updates", subgraphs=True)` 的 chunk 是：

```python
(namespace, update)
```

- `namespace == ()`：顶层节点；继续 `final.update(update)` 和 span 记录；
- `namespace != ()`：子图内部节点；只用于 `progress`，**不写 `final`**。

维护一个 `seen` dict 合并内部 update，用来读取 `refund_amount` / `pending_question` 等中间值。

**Step 3: 发帧**

- 节点完成时发：

```json
{"event": "progress", "stage": "checking_order", "status": "done"}
{"event": "progress", "stage": "amount_ready", "status": "done", "amount": 124900}
{"event": "progress", "stage": "waiting_info", "status": "waiting", "pending_question": "order_id"}
```

- 最终 progress 在 `delta` 前发，优先级：

```
handoff > waiting_info > waiting_confirm > answering / data_ready / working
```

- `amount` 只在可退且合规时带；`compliance_violation` 非空时**不发** `amount_ready`，最终走 `handoff`；
- `evidence_ready` 只在 `judge_evidence` 判定证据足够时发；弱证据不发；
- mapping 异常只记日志、跳过这一帧，不能让聊天失败。

**Step 4: 测试**

- `FakeGraph.astream` 接受 `subgraphs=True`，并能产出 `(namespace, update)`；
- rules 流程：`checking_order` → `amount_ready` → `waiting_confirm`；
- 追问流程：`waiting_info` 带 `pending_question`；
- knowledge：`retrieving` → `evidence_ready` → `answering`；
- 不可退/转人工：不出现 `amount_ready`；
- 帧里不含节点名/规则 id/置信度。

**Commit:** `feat(ch08): 子图进度事件`

---

## Task 6: 转人工结构化入口

**目的：** 按钮点击真的能转人工，不靠再分类、不建工单。

**Files:**
- Modify: `app/api/chat.py`
- Modify: `tests/api/test_chat_sse.py`

**Step 1: 短路**

在槽位填充之前判断：

```python
if (selected_action or {}).get("type") == "transfer_human":
    # 不跑图
```

**Step 2: 复用 `human_exit`**

```python
final = {
    "route": "human",
    "intent": "其他",
    **await human_exit({}),
}
```

固定话术来自 `app/graph/nodes.py › human_exit`；
`actions` 仍是 `[{"type": "transfer_human"}]`；
**不创建 tickets 行**。

**Step 3: 保存与发帧**

- 保存用户消息（`message` 可为空，`meta.selected_action` 记录动作）；
- 发 `ack`；
- 发 `route`（`route=human`）；
- 发 `progress(handoff)`；
- guard → 同事务保存 assistant + flow（`decide_flow` 会清空当前流程）；
- 发 `delta` / `actions` / `done`。

**Step 4: 测试**

- 不跑图（FakeGraph 调用 0 次）；
- 返回固定话术 + `transfer_human`；
- `tickets` 表无新增；
- flow 被清空；
- 用同一 `client_message_id` 重试能回放。

**Commit:** `feat(ch08): 转人工结构化入口`

---

## Task 7: 前端脚手架 + 请求层

**目的：** 起一个能开发、能测试、能构建的 React 应用。

**Files:**
- Create: `client/package.json`、`client/vite.config.ts`、`client/tsconfig.json`
- Create: `client/index.html`、`client/src/main.tsx`
- Create: `client/src/lib/base.ts`、`client/src/lib/sse.ts`、`client/src/lib/api.ts`
- Create: `client/src/lib/sse.test.ts`

**Step 1: 脚手架**

- Vite + React + TypeScript；
- `package.json` scripts：`dev` / `build` / `typecheck` / `test`；
- `engines.node >= 20`；
- Vite proxy：

```ts
server: {
  proxy: { "/api": "http://127.0.0.1:8080" },
}
```

**Step 2: `base.ts`**

- `BASE = import.meta.env.VITE_API_BASE ?? ""`；
- 每个请求带 `X-Trace-Id`（`crypto.randomUUID()`）；
- 统一错误映射：404 / 429 / 5xx；
- 抛出 `ApiError(status, code, message)`。

**Step 3: `sse.ts`**

- `sendChat({ message, conversationId, clientMessageId, selectedAction, signal, onFrame })`；
- `fetch` POST `/api/chat`，读 `ReadableStream`；
- 解析 `data: {...}\n\n`；
- 处理分块到达、CRLF、keepalive/注释；
- 不认识的帧忽略；
- 重试时**复用** `clientMessageId`，不重新生成。

**Step 4: `api.ts`**

```ts
listConversations(): Promise<ConversationSummary[]>
getMessages(cid: number): Promise<ConversationHistory>
```

**Step 5: 测试**

- `sse.test.ts`：分块、CRLF、多行 data、未知帧；
- `base.ts` 错误映射；
- `npm run typecheck` / `npm run test` / `npm run build` 通过。

**Commit:** `feat(ch08): 前端脚手架与 SSE 请求层`

---

## Task 8: 聊天窗 + 帧 reducer + 错误处理

**目的：** 把 SSE 帧变成可渲染的消息列表。

**Files:**
- Create: `client/src/state/turn.ts`
- Create: `client/src/components/ChatWindow.tsx`
- Create: `client/src/components/MessageBubble.tsx`
- Create: `client/src/components/Composer.tsx`
- Modify: `client/src/App.tsx`
- Create: `client/src/state/turn.test.ts`

**Step 1: reducer 状态**

```ts
type TurnState = {
  conversationId: number | null;
  inFlight: boolean;
  messages: Message[];
  progress: Progress[];
  route: string | null;
  error: string | null;
  lastClientMessageId: string | null;
};
```

处理帧：

| 帧 | 行为 |
|---|---|
| `ack` | 记录 conversation/user message id；`inFlight=true` |
| `route` | 记录 route/intent；初始化步骤 |
| `progress` | 合并 stage；`done` 覆盖 `running`；`waiting` 终态 |
| `delta` | 写入最后一条 assistant 草稿 |
| `citations` | 挂到最后一条 assistant |
| `actions` | 挂到最后一条 assistant |
| `done` | 记录 assistant id；`inFlight=false` |
| `error` | 标记错误；`inFlight=false`；保留用户输入 |

**Step 2: 发送/停止**

- 发送前生成 `client_message_id`，写入 `localStorage`；
- 流式期间禁用发送；
- 停止用 `AbortController.abort()`；
- 错误映射：
  - 404 → 清空当前会话；
  - 429 → 提示“还在处理”，禁用 2–5 秒；
  - 5xx / timeout → 保留输入，显示重试。

**Step 3: 渲染**

- 回答按纯文本 + 换行；
- `[1]` 角标映射 `citation_ids` 的来源标题；
- 不使用 `dangerouslySetInnerHTML`；
- 不展示规则 id / 置信度 / 工具链。

**Step 4: 测试**

- 帧顺序：`ack → route → progress* → delta → citations → actions → done`；
- 未知帧/未知 stage 忽略；
- `error` 后保留用户输入；
- 429/404/5xx 映射。

**Commit:** `feat(ch08): 聊天窗与帧 reducer`

---

## Task 9: 进度面板 + 动作卡片 + 转人工

**目的：** 等待期间有反馈，后端动作可操作。

**Files:**
- Create: `client/src/components/ProgressPanel.tsx`
- Create: `client/src/components/ActionBar.tsx`
- Modify: `client/src/App.tsx`、`client/src/styles.css`
- Create: `client/src/components/actions.test.tsx`

**Step 1: 进度面板**

退款：

```
✓ 已核对订单
✓ 已算出金额 ¥1249.00
○ 等待您确认
```

追问时替换为：

```
✓ 已核对订单
○ 等待补充订单号
```

知识：

```
✓ 已检索知识库
✓ 已找到依据
○ 已整理回答
```

规则：

- 金额只来自 `progress.amount`，前端不自己算；
- 不展示内部字段；
- 移动端折叠成一行状态条。

**Step 2: ActionBar**

| action.type | UI | 点击行为 |
|---|---|---|
| `transfer_human` | “转人工”按钮 | `selected_action={type:"transfer_human"}`；成功后禁用 |
| `select_order` | 订单卡片 | `{type:"select_order", order_id}` |
| `select_sku` | 型号卡片 | `{type:"select_sku", sku_id}` |
| `select_spec` | 规格选项 | `{type:"select_spec", spec}` |
| `resume_flow` | “继续退款/继续” | 发文本“继续退费”或“继续” |

**Step 3: 测试**

- 三种退款状态切换；
- 转人工按钮触发正确 payload；
- 订单/型号/规格卡片 payload；
- 金额格式化（分 → 元）。

**Commit:** `feat(ch08): 进度面板与动作卡片`

---

## Task 10: 历史恢复 + 会话列表

**目的：** 刷新/换设备后能回到之前的会话。

**Files:**
- Create: `client/src/components/ConversationList.tsx`
- Create: `client/src/state/reconnect.ts`
- Modify: `client/src/App.tsx`
- Create: `client/src/state/reconnect.test.ts`

**Step 1: 启动恢复**

- 优先 URL `?c={id}`；
- 其次 `localStorage.lastConversationId`；
- 有 id → `GET /messages`；
- 无 id → `GET /conversations`：有历史选最近一条，否则新会话。

**Step 2: 历史渲染**

- user/assistant 气泡；
- assistant 的 `meta.actions` 恢复按钮；
- `meta.signals.amount` / `pending_question` 恢复进度面板最终状态；
- 引用标题恢复。

**Step 3: 会话列表**

- 只读最近 20 条；
- 显示 title、更新时间、未完成流程标记；
- 点击切换会话；
- “新对话”按钮清空当前会话。

**Step 4: 测试**

- URL/localStorage 恢复；
- 列表排序；
- 历史动作/金额恢复；
- 切换会话不串消息。

**Commit:** `feat(ch08): 历史恢复与会话列表`

---

## Task 11: 断流重连（C2-lite）

**目的：** 断流/刷新后不重复、不跳变。

**Files:**
- Modify: `client/src/state/reconnect.ts`
- Modify: `client/src/components/ChatWindow.tsx` / `Composer.tsx`
- Modify: `client/src/state/reconnect.test.ts`

**Step 1: 异常关闭**

没有收到 `done` 时：

- 保留本地已收到的内容；
- 调 `GET /messages` 对账；
- 如果服务端有 `reply_to_client_message_id === 本地 client_message_id` 的 assistant：
  - 用服务端内容替换本地草稿；
  - 补 actions/signals；
  - 标记完成。
- 否则显示“连接中断，继续”；
- 历史请求失败时保留本地状态，显示网络错误。

**Step 2: 重试**

- 用户点击“继续”才重试；
- 用**同一个** `client_message_id` 和 `conversation_id`；
- 不自动无限重试；
- 429 → 提示“还在处理”，稍后再试。

**Step 3: 停止**

- `AbortController.abort()`；
- 本地标记“已停止”；
- 允许用同一 id 重试。

**边界（写进验收报告）**

- `ack` 丢失时前端没有 `conversation_id`，重试会新建会话；
  单会话 UI 不展示孤儿会话，跨会话去重不在 Ch08 范围（见 spec §7.4 / TODO P3 #8）。
- 原请求仍在跑时，重试会收到 429 `busy`；提示“还在处理”，不自动重试。

**Step 4: 测试**

- 服务端有回复 → 替换本地草稿；
- 服务端没有 → 显示继续；
- 重试请求复用同一 id；
- 合并后不重复；无可见措辞跳变。

**Commit:** `feat(ch08): C2-lite 断流恢复`

---

## Task 12: 测试、四场景验收、文档

**目的：** 把 Ch08 变成可演示、可复现的交付。

**Files:**
- Create: `docs/reports/ch08-acceptance.md`
- Modify: `docs/reports/ch08-frontend-changes.md`
- Modify: `docs/reports/00-change-log-overview.md`（Ch08 行）
- Modify: `docs/reports/README.md`
- Modify: `README.md`（快速开始：前端两条命令 + 迁移命令）
- Modify: `docs/reports/todo.md`（关闭 P0 / P3 迁移项）

**Step 1: 后端回归**

```bash
uv run pytest -q
ruff check .
```

基线不得倒退；新增测试必须覆盖：

- 迁移幂等；
- 唯一回复索引；
- 保存失败 fail-closed；
- 重试回放不重复；
- 历史接口归属/白名单；
- progress 帧；
- 转人工短路。

**Step 2: 前端门槛**

```bash
cd client
npm run typecheck
npm run test
npm run build
```

**Step 3: 四个演示场景**

| # | 输入 | 期望 |
|---|---|---|
| 1 | “订单1002 不想要了” | rules；核对中 → 已算出 ¥1249.00 → 待确认；金额只出现一次 |
| 2 | 已确认的转人工样本（如“用了一下，不太确定算不算用过，能退吗”） | 转人工；按钮；点击固定话术；无工单 |
| 3 | “退货政策是什么” | knowledge；检索 → 有依据 → 回答；引用标题可见 |
| 4 | “我要退款” → “退货政策是什么” → “继续退费” → “1002” | 第 2 轮正常答且提示未完成；回来不重复问原因，完成退款 |

**Step 4: 断流演练**

1. 发一轮，DevTools 切 Offline，等 SSE 断；
2. 恢复网络，点“继续”；
3. 验证：同一 `client_message_id`；数据库只有一条 user + 一条 assistant；无重复气泡；措辞不跳变；
4. `delta` 后断开并刷新：历史恢复回答、金额、动作卡片。

**Step 5: 写验收报告**

`docs/reports/ch08-acceptance.md` 至少包含：

- 四场景截图/录屏；
- 断流演练结果；
- 后端/前端回归命令与结果；
- 已知边界（ack 丢失、token 级流式不做、无工单）；
- 与 `todo.md` 的关闭项对应。

**Step 6: 同步文档**

- `README.md` 快速开始加：

```bash
# 后端
uv run python scripts/migrate_ch08.py --db data/app.db
uv run uvicorn app.main:app --reload --port 8080

# 前端
cd client && npm install && npm run dev
```

- `docs/reports/todo.md`：关闭 P0-2 全部、P3 #2 迁移、P3 #7 `diff` 修正；
  保留 P3 #8（ack 丢失边界）与 P4 触发项。
- `docs/reports/00-change-log-overview.md`：Ch08 状态改为 ✅。

**Commit:** `docs(ch08): 四场景验收与交付文档`

---

## 任务顺序与依赖

```
Task 0（spike）
  → Task 1（数据层）
  → Task 2（保存契约）
  → Task 3（回放）
  → Task 4（历史接口）
  → Task 5（progress）
  → Task 6（转人工）
  → Task 7（前端脚手架）
  → Task 8（聊天）
  → Task 9（进度/动作）
  → Task 10（历史/列表）
  → Task 11（断流）
  → Task 12（验收）
```

- Task 7 可以在 Task 4 之后并行开始，但一个人做建议按顺序。
- Task 5 依赖 Task 0 的 spike 结论；spike 不通过则走降级方案并更新 spec。
- Task 3 依赖 Task 1 的列/索引；Task 2 依赖 Task 1 的 `save_assistant_message` 参数。

---

## 风险与回退

| 风险 | 回退 |
|---|---|
| `subgraphs=True` 在项目图上不可用/成本高 | 走 P0-1 #4 降级：route + 计时 + 最终状态；更新 spec §6.2/§14 |
| 老库迁移失败 | 先备份 `data/app.db`；失败则按 Ch03 约定重建库 + seed |
| 重试并发写出两条回复 | `uk_msg_reply` + 会话锁；IntegrityError 取回已写回复 |
| 保存失败导致用户拿不到回答 | fail-closed + 明确错误文案 + 重试；用 `turn:persist` P50/P95 观察 |
| 前端范围蔓延到 B 端 | 只认四场景验收；spec §11 明确排除 |
| 引用/动作泄露内部信息 | 历史接口白名单 + 前端只渲染语义字段 |

---

## 完成定义（DoD）

- [ ] 后端：`uv run pytest -q` 全绿；`ruff check .` 通过。
- [ ] 前端：`npm run typecheck` / `npm run test` / `npm run build` 通过。
- [ ] 四个演示场景在界面上走通，有截图/录屏。
- [ ] 断流演练通过：同一 `client_message_id` 只有一条 user + 一条 assistant。
- [ ] 刷新后消息、引用、动作、金额能恢复。
- [ ] `docs/reports/ch08-acceptance.md` / `ch08-frontend-changes.md` 完成。
- [ ] `README.md` 有前后端启动与迁移命令。
- [ ] `todo.md` 关闭 P0 / P3 迁移与 `diff` 修正；保留 P3 #8 边界与触发型 P4。
- [ ] 没有自动 commit / push；提交由用户明确指示。
