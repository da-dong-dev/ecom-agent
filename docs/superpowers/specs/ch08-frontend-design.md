# Ch08 前端设计

> 依据：L4（前端范围 B：聊天 + 轻量进度）、C2（断流处理）、D4（转人工动作）、T6（React + Vite）、L8（四场景演示）。
> 范围：**聊天窗 / 进度面板 / 转人工入口 / 断流恢复 + 支撑这些能力的最小后端契约**。
> 状态：设计稿（2026-11-13）。C2-lite 方向已确认；本稿按实际代码核对后重写前置条件与工作量。

---

## 0. 现状核对（先纠正事实）

本稿不假设任何接口已经存在。以下结论来自对当前代码的逐项核对：

| # | 能力 | 实际现状 | 证据 |
|---|---|---|---|
| 1 | 聊天 SSE | 已有 `POST /api/chat`；帧顺序 `ack → route → delta → citations → actions → done` | `app/main.py:81-128`、`app/api/chat.py:321-450` |
| 2 | `delta` | **整段一帧**，不是 token 级流式 | `app/api/chat.py:402-403` |
| 3 | 会话列表 HTTP 接口 | **不存在**；数据层已有 `list_conversations` | `app/db/repo_conversation.py:87-97` |
| 4 | 历史消息 HTTP 接口 | **不存在**；数据层已有 `list_messages` / `load_messages` | `app/db/repo_message.py:56-66`、`app/core/history.py:114-131` |
| 5 | `client_message_id` 幂等 | **只对用户消息做幂等**；重复提交仍会跑图 | `app/db/repo_message.py:18-53`、`app/api/chat.py:298-304`、`app/api/chat.py:333` |
| 6 | 助手消息 `meta` | 只有 `intent / route / citation_ids / resumed`；缺 `actions`、金额/信号、`pending_question` | `app/api/chat.py:429-444` |
| 7 | 重试关联 | 没有“按 `client_message_id` 查助手回复”的字段或索引 | `app/db/models.py:61-91`、`app/db/schema.sql:22-39` |
| 8 | 保存顺序 | `yield delta` 之后才保存 assistant + flow | `app/api/chat.py:402-450` |
| 9 | 进度事件 | 只有 `route` 帧；没有 `progress` 帧 | `app/api/chat.py:351-357` |
| 10 | 转人工 | 只返回 `transfer_human` 动作；没有结构化入口 | `app/graph/nodes.py:123-134`、`app/graph/transfer.py` |
| 11 | 迁移机制 | `init_db.py` 用 `create_all` + schema 比对；**没有 ALTER 迁移** | `scripts/init_db.py` |
| 12 | LangGraph | 1.2.18；用**同版本最小图**验证 `astream(..., subgraphs=True)` 能拿到子图节点事件；项目图接入前先做 spike | `pyproject.toml` |

**结论**：Ch08 不是“只写前端”。8.2 进度面板、8.4 断流恢复、8.3 动作按钮都需要后端先把契约补齐，否则只能做假 UI。

---

## 1. 目标

### 1.1 要解决的问题

1. **可演示**：四个场景（正常退费 / 边界转人工 / 知识问答 / 中途离开再回来）在界面上走通，不依赖 curl。
2. **等待焦虑**：退款一轮约 4–15 秒；当前 `route` 之后到 `delta` 之间没有任何反馈。进度面板要显式展示“在核对 / 已算出 / 待确认”。
3. **动作可操作**：后端已返回 `transfer_human` / `select_order` / `select_sku` / `select_spec` / `resume_flow`，前端要能渲染并继续流程。
4. **中断可恢复**：刷新或 SSE 断流后能补历史；重试不能重复执行，不能出现两遍回答或措辞跳变。

### 1.2 目标

1. 新建 `client/`：React + Vite + TypeScript 单页应用。
2. 聊天窗支持 SSE 帧渲染、发送/停止、错误映射。
3. 进度面板按路由展示步骤；退款固定三态：核对中 → 已算出金额 → 待确认/待补充。
4. 动作卡片可点击；转人工按钮走确定性 D 出口，不建工单。
5. 刷新/断流后能通过历史接口恢复；重试复用同一个 `client_message_id`。
6. 补齐支撑上述能力的最小后端契约：历史接口、助手消息持久化、重试回放、进度事件、转人工短路。

### 1.3 验收标准

1. 四个演示场景在界面上走通，见 §9.3。
2. 进度面板不展示规则 id、置信度、工具调用链、相似度分数。
3. 同一 `client_message_id` 重试后，数据库中只有一条用户消息、一条助手回复。
4. 刷新页面后，已完成轮次的消息、引用、动作卡片、金额都能恢复。
5. 后端全量测试通过；前端 `typecheck / test / build` 通过。
6. 交付 `docs/reports/ch08-acceptance.md`，含四场景截图/录屏、断流演练、回归结果。

---

## 2. 技术栈

| 层 | 选择 | 说明 |
|---|---|---|
| 前端框架 | React + Vite + TypeScript | T6 已定；单页应用，不需要 Next.js 的服务端能力 |
| 测试 | Vitest + React Testing Library + jsdom | 纯函数与组件测试；Ch08 不引入 Playwright |
| 样式 | CSS Modules / plain CSS | 不引入 UI 框架，避免把演示项目做成组件库练习 |
| 请求 | `fetch` + `ReadableStream` | 不引入 axios；`AbortController` 支持停止 |
| 开发联调 | Vite proxy `/api` → `127.0.0.1:8090` | 不改后端 CORS |
| 后端 | 沿用 FastAPI + SQLAlchemy + SQLite | 不引入新运行时依赖 |
| 流式 | LangGraph `astream(..., subgraphs=True)` | 1.2.18 同版本最小图已验证；项目图接入前先做 spike |

Node 版本：20+；在 `client/package.json` 写 `engines`，可选加 `.nvmrc`。

---

## 3. 架构

```
Browser (React)
  ├─ lib/sse.ts     POST /api/chat，解析 SSE 帧
  ├─ lib/api.ts     GET /api/conversations
  │                 GET /api/conversations/{id}/messages
  ├─ state/turn.ts  帧 reducer + 重连合并
  └─ components     ChatWindow / ProgressPanel / ActionBar / ConversationList
        │
        ▼
FastAPI
  ├─ POST /api/chat
  │    run_turn:
  │      1. 会话锁
  │      2. 重试回放检查（reply_to_client_message_id）
  │      3. 槽位填充 / 转人工短路 / 跑图
  │      4. 图节点 → progress 帧
  │      5. 输出守卫
  │      6. 同事务保存 assistant + flow
  │      7. yield 最终 progress → delta → citations → actions → done
  ├─ GET /api/conversations
  └─ GET /api/conversations/{id}/messages
```

**状态归属**

- 会话级状态（`conversation_id`、`client_message_id`）由前端持久化到 `localStorage`，并尽量同步到 URL `?c=`。
- 跨轮业务流程状态仍在后端 DB 的 `active_flow / pending_question / draft`，前端不复制、不推断。
- 前端只维护“本页面正在展示什么”：消息列表、进度步骤、当前错误。

---

## 4. 目录结构

```
client/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    lib/
      base.ts          # fetch 封装、trace_id、错误映射
      sse.ts           # SSE 解析、AbortController、client_message_id
      api.ts           # chat / listConversations / getMessages
    state/
      turn.ts          # 帧 reducer、进度合并、重连合并
    components/
      ChatWindow.tsx
      MessageBubble.tsx
      Composer.tsx
      ProgressPanel.tsx
      ActionBar.tsx
      ConversationList.tsx
    styles.css
    test/
      sse.test.ts
      turn.test.ts
      reconnect.test.ts
      actions.test.tsx
```

规则：组件里不允许直接出现 `fetch`，只能调 `lib/api.ts` / `lib/sse.ts`。

---

## 5. 数据模型变更

### 5.1 新增列：`messages.reply_to_client_message_id`

**为什么用独立列，不塞进 `meta` JSON**

| 方案 | 优点 | 缺点 |
|---|---|---|
| 塞进 `meta` | 不改表 | 无法建索引；每次回放都要解析 JSON；无法用唯一约束防重复 |
| **独立列 + 唯一部分索引** | 可索引、可约束、可查询；语义清楚 | 需要迁移老库 |

**新库 DDL（`app/db/schema.sql`）**

在 `CREATE TABLE messages` 中增加一列：

```sql
reply_to_client_message_id TEXT,   -- 关联到触发本轮的用户消息（K4 重试回放）
```

并增加索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uk_msg_reply
  ON messages(conversation_id, reply_to_client_message_id)
  WHERE reply_to_client_message_id IS NOT NULL;
```

**老库迁移**：`schema.sql` 里的 `CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列，必须由 `scripts/migrate_ch08.py` 执行 `ALTER TABLE messages ADD COLUMN ...`。新库由 `Base.metadata.create_all` 直接建列。

**ORM（`app/db/models.py`）**

```python
reply_to_client_message_id: Mapped[str | None] = mapped_column(String)
```

并在 `__table_args__` 增加：

```python
Index(
    "uk_msg_reply",
    "conversation_id",
    "reply_to_client_message_id",
    unique=True,
    sqlite_where=text("reply_to_client_message_id IS NOT NULL"),
)
```

**语义**

- 只写在**成功保存的助手消息**上，值等于触发它的用户消息的 `client_message_id`。
- 错误消息（`status="error"`）不写，保证用户重试时不会回放一个错误结果。
- 旧消息没有该值，保持 `NULL`；历史接口照常返回，不影响展示。

### 5.2 助手消息 `meta` 的持久化字段

当前只有 `intent / route / citation_ids / resumed`。新增：

```json
{
  "intent": "退款申请",
  "route": "rules",
  "citation_ids": [],
  "resumed": false,
  "actions": [{"type": "transfer_human"}],
  "signals": {
    "amount": 124900,
    "pending_question": "order_id",
    "refund_eligible": true
  }
}
```

| 字段 | 用途 | 约束 |
|---|---|---|
| `actions` | 刷新后恢复按钮/卡片 | 原样持久化，前端按 `type` 渲染 |
| `signals.amount` | 进度面板“已算出金额” | 单位**分**；仅退款可退且合规时写 |
| `signals.pending_question` | “等待您补充…” | 只写槽位键，不写内部规则 |
| `signals.refund_eligible` | 区分“可退/不可退” | 可选；不展示置信度 |

**不持久化**：`order_snapshot`、`refund_judgement` 的完整内容、`tool_code`、`trace_id`、`draft`、任何置信度/规则 id。历史接口也不返回。

### 5.3 迁移脚本

`scripts/init_db.py` 使用 `create_all`，**不会给已存在的表加列**。因此新增：

```
scripts/migrate_ch08.py --db data/app.db
scripts/migrate_ch08.py --db data/eval.db
```

要求：

- 幂等：先查 `PRAGMA table_info(messages)`，缺列才 `ALTER TABLE`；索引用 `CREATE ... IF NOT EXISTS`。
- 可测试：用旧 schema 建临时库 → 跑两次迁移 → 断言列与索引存在。
- 如果接受丢本地会话，也可以 `rm data/app.db && uv run python scripts/init_db.py && uv run python scripts/seed.py`；迁移脚本是保留数据的默认路径。

顺带修正 `scripts/init_db.py` 的 `diff`：当前只检查 `schema.sql` 独有的索引，不检查 ORM 独有的索引；新增索引时补上双向检查，避免漂移漏检。

---

## 6. 接口契约

### 6.1 SSE 帧（在现有基础上新增 `progress`）

帧顺序：

```
ack → route → progress* → delta → citations → actions → done
```

异常时用 `error` 帧替换其后所有帧。

| 帧 | 现状 | Ch08 变化 |
|---|---|---|
| `ack` | 已存在 | 不变；重试回放时返回**已有**的 `user_message_id` |
| `route` | 已存在 | 不变；转人工短路时也会发 |
| `progress` | **新增** | 语义步骤，见 §6.2 |
| `delta` | 已存在 | 仍是整段一帧；在持久化成功后才发 |
| `citations` | 已存在 | 不变 |
| `actions` | 已存在 | 不变 |
| `done` | 已存在 | 不变；持久化失败时不发 |
| `error` | 已存在 | 持久化失败也走这里（`internal_error`） |

### 6.2 `progress` 帧

```json
{"event": "progress", "stage": "checking_order", "status": "done"}
{"event": "progress", "stage": "amount_ready", "status": "done", "amount": 124900}
{"event": "progress", "stage": "waiting_info", "status": "waiting", "pending_question": "order_id"}
{"event": "progress", "stage": "handoff", "status": "waiting"}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage` | 枚举 | 语义步骤，不是节点名、不是规则 id |
| `status` | `done` / `waiting` / `error` | `running` 由前端根据上一步推导，不要求后端发 |
| `amount` | int（分） | 仅 `amount_ready` 可带 |
| `pending_question` | 槽位键 | 仅 `waiting_info` 可带 |

**stage 词表**

| 路由 | stage | 触发时机 | 前端文案 |
|---|---|---|---|
| `rules` | `checking_order` | `query_data` 完成 | 已核对订单 |
| `rules` | `amount_ready` | `compliance` 通过且有可退金额 | 已算出金额 |
| `rules` | `waiting_info` | `need_info` 产生 `pending_question` | 等待您补充信息 |
| `rules` | `waiting_confirm` | 回答就绪、可退金额已知且无追问 | 等待您确认 |
| `rules` | `answering` | 回答就绪但不可退/无金额 | 已整理回答 |
| `data` | `querying_data` | `query` 完成 | 已查到数据 |
| `data` | `data_ready` | `format` 完成 | 已整理回答 |
| `data` | `waiting_info` | `need_info` 产生 `pending_question` | 等待您补充信息 |
| `knowledge` | `retrieving` | `retrieve` 完成 | 已检索知识库 |
| `knowledge` | `evidence_ready` | `judge_evidence` 判定证据足够 | 已找到依据 |
| `knowledge` | `answering` | `generate` 完成 | 已整理回答 |
| `fallback` | `working` | `gate` / `agent` / `scripted` 完成 | 已处理 |
| `human` | `handoff` | 进入转人工 | 正在转接人工 |

**规则**：后端只发语义 stage；前端负责中文文案。未知 `stage` 直接忽略，不能让聊天失败。

**最终 progress 的优先级**（只发一条）：

```
handoff（有 transfer_human）
  > waiting_info（有 pending_question）
  > waiting_confirm（rules 有 amount 且无追问）
  > answering / data_ready / working
```

### 6.3 保存顺序与失败行为（关键变更）

**现状**：先 `yield delta`，再保存 assistant + flow（`app/api/chat.py:402-450`）。

**改为**：

```
输出守卫 → 计算 actions/flow decision
        → 同一事务保存 assistant + apply_flow_decision
        → 成功后才 yield 最终 progress / delta / citations / actions / done
```

**保存失败时（fail-closed，失败按不通过处理）**：

- 不回 `delta`、`citations`、`actions`、`done`；
- 抛 `ChatError("internal_error", ...)`，由 `app/main.py` 的 SSE 包装转成 `error` 帧；
- 用户消息已经在前面保存，重试时用同一个 `client_message_id` 可以重跑；
- 记录日志与 `turn:total` span，标注 `persisted=false`。

**为什么 fail-closed**：C2-lite 的不变式是“**用户看到的回答一定已落库**”。如果先展示再保存，保存失败时用户看到了回答，刷新后却消失，重试还可能生成不同措辞——这正是“跳变”。

**代价**：数据库瞬时故障时，即使模型已经生成回答，用户也看不到，只能重试。对本地 SQLite 演示可以接受；换来的是历史与重试的一致性。

### 6.4 重试回放

**触发**：`POST /api/chat` 携带的 `client_message_id` 在当前会话中已存在。

**流程**（在会话锁内、跑图之前）：

1. 查 `messages` 中 `conversation_id = cid AND reply_to_client_message_id = client_message_id` 的助手消息。
2. 命中 → 不跑图、不改 flow，直接按已存内容回放：
   - `ack`（已有 `user_message_id`）
   - `route`（`meta.route` / `meta.intent` / `meta.resumed`）
   - `progress`（由 `meta.signals` 还原最终状态）
   - `delta`（已存 `content`）
   - `citations`（`meta.citation_ids`）
   - `actions`（`meta.actions`）
   - `done`（已有 assistant `message_id`）
3. 未命中 → 正常跑图。
4. 并发情况下，唯一部分索引 `uk_msg_reply` 兜底：两个请求同时写同一条回复时，一个成功，另一个取回已写的那条并回放。

**前端约束**：

- 同一个 `client_message_id` 只对应一条用户消息；**重试必须复用，不能重新生成**。
- 不同消息不能复用同一个 id，否则会被当成重试。

**边界**：

- 如果 `ack` 在断流前就丢了，前端没有 `conversation_id`；此时重试会新建会话，原会话可能成为孤儿。Ch08 的单会话 UI 不会展示孤儿会话；跨会话去重不在本章范围。
- 如果原请求仍在运行，重试会遇到会话锁的 `busy`（429）；前端提示“还在处理”，稍后重试。

### 6.5 `GET /api/conversations`

**用途**：刷新/换设备后找回会话；前端左侧轻量会话列表。

**请求**：无参数；当前使用 `settings.demo_user_id`，Ch06 接真实鉴权后改为登录用户。

**响应 200**：

```json
{
  "items": [
    {
      "id": 12,
      "created_at": "2026-11-13T08:00:00",
      "updated_at": "2026-11-13T08:02:00",
      "title": "订单1002 不想要了",
      "active_flow": "refund",
      "pending_question": "reason_code"
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `title` | 第一条非空用户消息截断 30 字；没有则 `"新会话"` |
| `active_flow` | `refund` / `data` / `null`；前端映射成“未完成退款/查询” |
| `pending_question` | 槽位键；前端映射成“等待订单号/原因” |

规则：

- 只返回当前用户、未软删的会话，`updated_at` 倒序，最多 20 条；Ch08 不做分页。
- `conversations.updated_at` 必须在每次消息写入时 touch；否则纯聊天、不写流程状态的会话会停在创建时间，掉出最近 20 条。
- 前端从 URL / localStorage 指向的会话如果不在最近 20 条：不加载旧会话，选中最近一条并更新 URL；列表为空时才回到新会话欢迎页。
- 不返回 `summary` / `draft` / 内部状态。
- 没有会话时返回 `{"items": []}`，不返回 404。
- **归属处理**：列表接口按 `user_id` 过滤，别人的会话不会出现在结果里，因此不需要单会话 404；单会话的消息接口见 §6.6，统一 404。

### 6.6 `GET /api/conversations/{id}/messages`

**用途**：刷新/断流后的历史对账。

**归属校验**：`get_conversation(cid, user_id=demo)`；不存在、不属于本人、已软删**统一 404**，不区分原因。

**响应 200**：

```json
{
  "conversation_id": 12,
  "active_flow": "refund",
  "pending_question": "order_id",
  "items": [
    {
      "id": 34,
      "role": "user",
      "content": "订单1002 不想要了",
      "created_at": "2026-11-13T08:01:00",
      "client_message_id": "uuid-1",
      "meta": {"selected_action": null}
    },
    {
      "id": 35,
      "role": "assistant",
      "content": "本次应退金额：1249.00 元。",
      "created_at": "2026-11-13T08:01:05",
      "status": "completed",
      "reply_to_client_message_id": "uuid-1",
      "meta": {
        "intent": "退款申请",
        "route": "rules",
        "citation_ids": [],
        "resumed": false,
        "actions": [],
        "signals": {
          "amount": 124900,
          "pending_question": null,
          "refund_eligible": true
        }
      }
    }
  ]
}
```

规则：

- 只返回 `role in ("user", "assistant")`；**绝不返回 tool 消息**。
- 按 `id` 升序。
- 字段白名单：不返回 `tool_calls` / `tool_call_id` / `trace_id` / `draft` / `order_snapshot` / `refund_judgement` / 置信度。
- `meta` 按角色过滤：
  - user：只允许 `selected_action`；
  - assistant：只允许 `intent / route / citation_ids / resumed / actions / signals`。
- `signals` 内部只允许 `amount / pending_question / refund_eligible`。
- 错误助手消息（`status="error"`）照常返回，但没有 `reply_to_client_message_id`。
- `client_message_id` / `reply_to_client_message_id` 是客户端生成的 UUID，返回给前端用于精确对账；不含内部信息。

### 6.7 转人工结构化入口

**请求**：

```json
{
  "conversation_id": 12,
  "message": "",
  "client_message_id": "uuid-2",
  "selected_action": {"type": "transfer_human"}
}
```

**行为**：

- 在槽位填充之前短路，不跑意图分类、不跑图。
- 复用 `human_exit` 的固定话术与动作（`app/graph/nodes.py:123-134`），**不建工单**。
- `route=human`；`route` 帧的 `intent` 固定为 `其他`（前端不依赖它做展示）；按 `decide_flow` 的规则清空当前流程。
- 仍然保存用户消息与助手回复（带 `reply_to_client_message_id`），因此可重试回放。
- 帧顺序：`ack → route → progress(handoff) → delta → actions → done`。

**前端**：`transfer_human` 按钮点击后禁用，避免连续点击；失败时恢复。

### 6.8 错误映射

沿用现有 SSE `error` 帧与 HTTP 错误：

| HTTP / code | 场景 | 前端行为 |
|---|---|---|
| 404 | 会话不存在 / 不属于本人 | 清空当前会话，回到新会话（不提示“无权限”） |
| 429 `busy` | 上一条还在处理 | 提示“还在处理”，禁用发送 2–5 秒 |
| 504 `timeout` | 模型/检索超时 | 保留输入，可重试 |
| `invalid_arg` | 空消息、超长 | 就地提示，不新增气泡 |
| `internal_error` | 落库失败等 | 保留用户输入，显示重试按钮 |

---

## 7. C2-lite（断流恢复的轻量落地）

### 7.1 定义

**C2 是设计文档里的编号**，来自 `docs/design/电商客服-并发与超时设计.md` 的「C2 · 流式断流」。原文选的是 **C：重连补历史 + 继续，不重新生成**。

**C2-lite** 是它在当前“整段 `delta`”事实下的轻量落地：

- 不存生成片段，不传 `last_offset`，不做后台续生成；
- 用 **历史对账 + 同一 `client_message_id` 回放/重试** 达到“不重复、不跳变”。

### 7.2 四种断流场景

| 断流时机 | 用户看到什么 | 重连后怎么做 | 结果 |
|---|---|---|---|
| 生成中，`delta` 之前 | 只有进度 | 历史里没有 assistant → 同 id 重试，服务端重跑 | 用户没看过半句话，重跑不跳变 |
| `delta` 之后，`done` 之前 | 完整回答 | 历史里已有 assistant → 直接渲染；动作从 `meta.actions` 补 | 不重跑，措辞不变 |
| 刷新页面 | 已完成的轮次 | `GET messages` 恢复全部 | 像没断过 |
| 已完成后再点重试 | 已有回答 | 服务端按 `reply_to_client_message_id` 回放 | 不重复执行、不重复扣成本 |

### 7.3 前置条件

C2-lite 成立需要以下全部条件；缺一个都只能降级为“重试可能重复”：

| # | 前置条件 | 当前状态 | 归属 |
|---|---|---|---|
| 1 | `GET /api/conversations` | 不存在（repo 有） | 后端 8.0 |
| 2 | `GET /api/conversations/{id}/messages` | 不存在（repo/loader 有） | 后端 8.0 |
| 3 | `messages.reply_to_client_message_id` + `uk_msg_reply` | 不存在 | 数据层 8.0 |
| 4 | 助手 `meta` 持久化 `actions` / `signals` | 不存在 | 后端 8.0 |
| 5 | 锁内按 `client_message_id` 查 assistant 并回放 | 不存在 | 后端 8.0 |
| 6 | 先同事务保存 assistant + flow，再 `yield delta`；失败 fail-closed | 当前顺序相反 | 后端 8.0 |
| 7 | 前端持久化 `client_message_id` / `conversation_id` | 不存在 | 前端 8.4 |
| 8 | 前端历史对账 + 重试合并 | 不存在 | 前端 8.4 |

### 7.4 能保证 / 不能保证

**能保证**

- 同一 `client_message_id` 最多一条用户消息、最多一条助手回复；
- 已展示的回答在刷新后能恢复；
- 已完成轮次的重试不重新生成；
- 当前原子 `delta` 下，用户不会看到半句话。

**不能保证**

- `ack` 丢失后的跨会话去重：前端没有 `conversation_id` 时会新建会话，原会话可能成为孤儿；单会话 UI 不展示，跨会话去重不在本章范围。
- token 级断点续生成。
- 多标签页并发：靠会话锁返回 429，前端提示稍后重试。
- 服务端在“已生成、未保存”之间崩溃：fail-closed 保证用户看不到未保存的回答。

### 7.5 升级触发条件

出现任一情况，C2-lite 不够用，必须升级为完整 C2（服务端片段 buffer + `last_offset`）：

1. 后端改成 token 级流式输出；
2. 用户真的看到半句话后断流；
3. 断流率超过 5%，需要精确续传。

---

## 8. 前端设计

### 8.1 页面结构

```
┌──────────────┬──────────────────────────┐
│ 历史会话列表  │  聊天区                   │
│ （可折叠）    │  消息气泡 + 动作卡片       │
│              │  ─────────────────────    │
│              │  输入框 / 发送 / 停止      │
│              ├──────────────────────────┤
│              │  进度面板                 │
└──────────────┴──────────────────────────┘
```

- 桌面：左侧会话列表 + 主区聊天 + 右侧/底部进度面板。
- 移动（≤ 640px）：会话列表收进抽屉；进度面板折叠成一行状态条。
- 不展示规则 id、置信度、工具链、相似度分数。

### 8.2 消息与进度状态

```ts
type Message = {
  id?: number;
  clientMessageId?: string;
  role: "user" | "assistant";
  content: string;
  status?: string;
  meta?: Record<string, unknown>;
};

type Progress = {
  stage: string;
  status: "running" | "done" | "waiting" | "error";
  amount?: number;            // 分
  pendingQuestion?: string;
};
```

Reducer 处理：

| 帧 | 行为 |
|---|---|
| `ack` | 记录 `conversation_id` / `user_message_id`；标记本轮进行中 |
| `route` | 记录 route/intent；初始化该路由的步骤列表 |
| `progress` | 合并步骤；`done` 覆盖 `running`；`waiting` 是终态；未知 stage 忽略 |
| `delta` | 写入最后一条 assistant 草稿（当前是整段，一次到位） |
| `citations` | 挂到最后一条 assistant |
| `actions` | 挂到最后一条 assistant；渲染按钮/卡片 |
| `done` | 记录 assistant `message_id`；本轮结束 |
| `error` | 标记当前步骤/本轮错误；保留用户输入与用户消息 |

进度只增不退：`done` 不会被后续 `running` 覆盖；重复 stage 幂等。

### 8.3 重连与历史合并

1. 发送前生成 `client_message_id` 并写入 `localStorage`（同一条消息的重试复用）。
2. 收到 `ack` 后写入 `conversation_id`。
3. 异常关闭且没有 `done`：
   - 保留本地已收到的内容；
   - 调 `GET /messages` 对账；
   - 如果服务端已有 `reply_to_client_message_id === 本地 client_message_id` 的 assistant：
     - 用服务端内容替换本地草稿，补 actions/signals，标记完成；
   - 否则显示“连接中断，继续”，用户点击后用同一 id 重试；
   - 如果历史请求本身也失败（离线/服务未起），保留本地状态并显示网络错误，不丢用户输入；
   - **不自动无限重试**，避免重复请求与 busy 循环。
4. 页面加载：
   - 优先 URL `?c={id}`，其次 `localStorage.lastConversationId`；
   - 有 id → `GET /messages` 恢复；
   - 无 id → `GET /conversations`，有历史则选最近一条，否则新会话。
5. 停止按钮：`AbortController.abort()`；本地标记“已停止”，允许用同一 id 重试。

### 8.4 进度面板

- 退款：`核对中 → 已算出金额 → 待确认`；追问时替换为`等待补充订单号/退款原因`。
- 知识：`检索知识库 → 找到依据 → 整理回答`。
- 数据：`查询数据 → 整理回答`。
- 转人工：`转接人工`。
- 金额只在后端给出 `amount` 时展示；前端不自己算。
- 每一步显示“已完成 / 进行中 / 等待用户 / 失败”，不显示内部字段。

### 8.5 动作渲染

| action.type | UI | 点击行为 |
|---|---|---|
| `transfer_human` | “转人工”按钮 | `selected_action={type:"transfer_human"}`；成功后禁用 |
| `select_order` | 订单卡片列表 | `selected_action={type:"select_order", order_id}` |
| `select_sku` | 规格卡片 | `selected_action={type:"select_sku", sku_id}` |
| `select_spec` | 规格选项 | `selected_action={type:"select_spec", spec}`（`sku_id` 已在上一轮 state 里） |
| `resume_flow` | “继续退款/继续查询”按钮 | 发送文本“继续退费”（退款）或“继续”（通用），沿用 `slot_fill` 的恢复逻辑 |

### 8.6 安全与渲染

- 回答按纯文本渲染，保留换行；`[1]` 角标映射到 `citation_ids` 的来源标题。
- 不使用 `dangerouslySetInnerHTML`；不渲染模型返回的 HTML。
- 引用只显示来源标题，不显示相似度、chunk id、规则 id。
- `client_message_id` 用 `crypto.randomUUID()`；不要用递增数字。

---

## 9. 测试与验证

### 9.1 后端测试

新增/修改：

| 文件 | 覆盖 |
|---|---|
| `tests/db/test_migrate_ch08.py` | 旧 schema → 迁移两次 → 列/索引存在；重复执行幂等 |
| `tests/db/test_repo_message.py` | `reply_to_client_message_id` 写入/查询；唯一索引阻止重复回复 |
| `tests/api/test_conversations_api.py` | 列表归属/排序/title；消息 404 统一；tool 消息过滤；meta 白名单 |
| `tests/api/test_chat_sse.py` | 帧顺序含 `progress`；保存先于 delta；保存失败 fail-closed；同 id 重试回放；transfer 短路 |
| `tests/api/test_eval_api.py` | 保存失败映射 500；原有评测行为不变 |

`FakeGraph.astream` 需要接受 `subgraphs` 参数；现有帧顺序断言需要允许/过滤 `progress` 帧。

### 9.2 前端测试

| 文件 | 覆盖 |
|---|---|
| `sse.test.ts` | 分块到达、CRLF、多行 `data:`、keepalive/注释、未知字段 |
| `turn.test.ts` | ack/route/progress/delta/citations/actions/done/error；进度单调；未知 stage 忽略 |
| `reconnect.test.ts` | 服务端有回复 → 替换本地草稿；没有 → 显示继续；同一 id 重试；去重 |
| `actions.test.tsx` | 转人工按钮、订单/规格卡片、resume_flow 按钮 |

### 9.3 四个演示场景（手工验收）

| # | 输入 | 期望 |
|---|---|---|
| 1 | “订单1002 不想要了” | `route=rules`；面板 核对中 → 已算出 ¥1249.00（以后端实际返回为准）→ 待确认；金额只出现一次 |
| 2 | 已确认的转人工样本（如“用了一下，不太确定算不算用过，能退吗”） | 面板显示转人工；`transfer_human` 按钮；点击后固定话术，不建工单 |
| 3 | “退货政策是什么” | `route=knowledge`；面板 检索 → 有依据 → 回答；引用标题可见，不显示相似度 |
| 4 | “我要退款” → “退货政策是什么” → 点“继续退款”/输入“继续退费” → “1002” | 第 2 轮正常答且提示未完成退款；回来不重复问原因，完成退款 |

### 9.4 断流演练

1. 正常发一轮，浏览器 DevTools 切 Offline，等 SSE 断；
2. 恢复网络，点“继续”；
3. 验证：对同一条 `client_message_id`，数据库只有一条用户消息、一条助手回复；无重复气泡；措辞不跳变。
4. 在 `delta` 之后断开，刷新页面：历史恢复回答、金额、动作卡片。

### 9.5 工程门槛

```bash
# 后端
uv run pytest -q
ruff check .

# 前端
cd client
npm run typecheck
npm run test
npm run build
```

- 基线：1030 passed / 16 skipped（Ch07 结束时）；新增测试不得破坏。
- `npm run build` 产物不含密钥；不把 `.env` 打进 client。
- `docs/reports/ch08-acceptance.md` 记录四场景、断流演练、回归结果。

---

## 10. 工作量

原 P8 的 3 天建立在“只写前端”的假设上。按实际代码核对后，必须补后端契约，工作量重估如下：

| 工作块 | 内容 | 估时（人日） |
|---|---|---|
| 后端 · 数据层 | 新列 + 唯一部分索引 + `append_message`/`save_assistant_message` + 迁移脚本 + 测试 | 0.75–1.0 |
| 后端 · 保存契约 | 先同事务保存 assistant + flow，再 delta；fail-closed；更新现有测试 | 0.5–1.0 |
| 后端 · 重试回放 | 锁内按 `reply_to_client_message_id` 查回复；回放帧；并发唯一索引兜底 | 0.5–1.0 |
| 后端 · 历史接口 | `GET /conversations`、`GET /conversations/{id}/messages`、字段白名单、归属 404、测试 | 0.5–0.75 |
| 后端 · 进度事件 | `subgraphs=True` spike、stage 映射、`progress` 帧、FakeGraph/帧顺序测试 | 0.75–1.0 |
| 后端 · 转人工短路 | `selected_action=transfer_human` 直通 `human_exit`，不跑图、不建单 | 0.25–0.5 |
| 前端 · 脚手架与请求层 | Vite/React/TS、proxy、`base.ts` / `sse.ts` / `api.ts` | 0.5 |
| 前端 · 聊天与 reducer | 气泡、发送/停止、帧 reducer、错误映射 | 1.0 |
| 前端 · 进度与动作 | 进度面板、动作卡片、转人工按钮 | 1.0 |
| 前端 · 历史与重连 | 会话列表、历史恢复、断流合并、重试 | 1.0 |
| 前端 · 测试与打磨 | 单测、响应式、四场景彩排、截图 | 0.5–1.0 |
| 验收与文档 | `ch08-acceptance.md`、README 快速开始、演示脚本 | 0.5–1.0 |
| **合计** | | **8–11 人日（约 2 周）** |

**拆分建议**（章节不拆，里程碑拆）：

- **Ch08-A 后端契约**：数据层 + 保存契约 + 回放 + 历史接口 + 进度 + 转人工（约 3.5–5 天）。
- **Ch08-B 前端**：脚手架 + 聊天 + 进度 + 动作 + 重连（约 4–4.5 天）。
- **验收**：四场景 + 断流演练 + 文档（约 0.5–1.5 天）。

如果后端契约由别人完成，前端本身仍是 4–5 天，不是 3 天。

---

## 11. 本章不做

| 项 | 原因 |
|---|---|
| token 级流式输出 | 当前 `delta` 整段；C2-lite 已能覆盖体验目标；升级触发见 §7.5 |
| 服务端片段 buffer / `Last-Event-ID` | 同上；完整 C2 是 token 流式的前置 |
| 真实退款提交 / 工单 / 人工坐席队列 | 写操作需要确认、幂等、权限、审计；D 出口仍只发动作 |
| B 端坐席工作台 | 后续配套；消费者看规则 id/置信度没有意义 |
| 规则 id、置信度、工具链、相似度展示 | L4 明确“给进度，不给内部细节” |
| 登录鉴权 / 多用户 | 当前 `demo_user_id`；Ch06 已有越权校验，真实鉴权后续 |
| 多会话搜索/重命名/删除 | Ch08 只做只读列表；K5 软删不暴露 |
| 会话分页 | 单用户演示数据量小；超过 20 条再补 |
| 生产部署 / 静态托管 | 本地 Vite + uvicorn 两进程即可；README 写清楚 |
| UI 组件库 / 深色模式 / 动画 | 不服务于验收；避免范围蔓延 |
| Playwright 端到端 | 单测 + 手工四场景足够；后续需要再补 |

---

## 12. 关键决策记录

| # | 决策 | 备选 | 选择 | 理由 | 代价 |
|---|---|---|---|---|---|
| D1 | 断流恢复 | 完整 C2 / C2-lite / 不支持 | **C2-lite** | 当前 `delta` 整段，用户看不到半句；历史+同 id 回放即可满足“不重复、不跳变” | 不支持 token 级续生成；升级时要补 buffer |
| D2 | 重试关联字段 | JSON meta / 独立列 / 不加 | **独立列 + 唯一部分索引** | 可索引、可约束、回放查询便宜 | 需要迁移老库 |
| D3 | 保存与展示顺序 | 先展示后保存 / 先保存后展示 | **先保存后展示，fail-closed** | “看到即已存”是 C2-lite 的不变式 | DB 故障时用户看不到已生成回答 |
| D4 | 助手 meta 内容 | 全量 state / 白名单 | **白名单：actions + signals** | 刷新可恢复动作/金额；不泄露内部字段 | 以后加 UI 字段要同步白名单 |
| D5 | 进度来源 | 定时器猜 / 顶层里程碑 / 子图事件 | **子图事件** | `subgraphs=True` 已验证；步骤与真实节点对齐 | 测试假图要适配；LangGraph 升级需回归 |
| D6 | 转人工入口 | 再分类 / 前端假装 / 结构化短路 | **结构化短路** | 确定性、可测试、不建单 | 需要新增一条 API 分支 |
| D7 | 会话恢复范围 | 只 localStorage / 列表接口 | **列表 + 最近会话** | 清缓存/换设备也能回来；T6 本来就规划了列表接口 | 多一个接口与轻量抽屉 |
| D8 | 前端状态 | 组件内 setState / reducer | **reducer** | 帧顺序、重连、去重都能单测 | 多一层状态文件 |
| D9 | 跨端口 | 后端 CORS / Vite proxy | **Vite proxy** | 不改生产代码；开发简单 | 生产部署需另配反向代理 |
| D10 | 回答渲染 | Markdown/HTML / 纯文本 | **纯文本 + 引用角标** | 避免 XSS；当前回答不需要复杂排版 | 富文本能力后续再评估 |

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| `subgraphs=True` 接入后帧结构变化 | 现有测试与 trace 逻辑要改 | 先在分支做 spike；只把子图事件用于 `progress`，`final` 仍取顶层 update；更新 `FakeGraph` |
| 老库没有新列 | 启动即报 `no such column` | 迁移脚本 + 启动前检查；README 写清执行顺序 |
| 重试并发 | 两条助手回复 | 唯一部分索引 + 会话锁；IntegrityError 取回已写回复 |
| 保存失败 fail-closed | 用户拿不到已生成回答 | 明确错误文案 + 重试；日志带 trace_id；本地 SQLite 故障率低 |
| `ack` 丢失 | 新会话、孤儿会话 | 单会话 UI 不展示；跨会话去重列为后续 |
| 进度事件泄露内部信息 | 面试/产品风险 | 只发语义 stage；前端文案表；后端不发节点名/规则 id |
| 范围蔓延到 B 端工作台 | 工期失控 | §11 明确排除；验收只认四场景 |
| 前端依赖 Node 工具链 | 后端同学跑不起来 | README 写 Node 20+ 与 `npm install`；前端可独立启动 |
| 断流演练依赖手工 | 回归不稳定 | 单测覆盖 reducer/合并；手工演练只验浏览器行为 |

---

## 14. 开放问题（写 plan 前需要确认）

1. **fail-closed 是否接受**：保存失败时宁可不展示回答，也不展示一个刷新后会消失的回答。
2. **唯一部分索引**：是否同意用 `uk_msg_reply` 在数据库层强约束“一条用户消息最多一条助手回复”。
3. **会话列表范围**：只做只读最近 20 条，不做搜索/重命名/删除。
4. **进度事件失败时的降级**：如果 `subgraphs=True` 接入成本高于预期，是否接受退化为“route + 计时提示 + 最终状态”（`amount_ready`/`waiting_confirm` 在 delta 前发出）。

以上四点确认后，进入 `docs/superpowers/plans/ch08-frontend.md` 的任务拆分；不在 spec 阶段写代码。
