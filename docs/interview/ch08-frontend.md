# Ch08 面试要点：C 端前端、SSE 与断流恢复

> 用途：这一章能讲什么、会被追问什么。每题给「结论 + 依据 + 出处」。
> 证据来源：[ch08-acceptance.md](../reports/ch08-acceptance.md)、
> [ch08-frontend-changes.md](../reports/ch08-frontend-changes.md)、
> [resume-positioning.md](../reports/resume-positioning.md)
> 状态：Ch08 已验收（2026-11-17）；2 分钟演示录屏待补。

---

## 0. 30 秒版本

> 「Ch08 把 Ch01–Ch07 的后端能力做成了可演示的 C 端界面：
> **React + Vite + TypeScript + antd**，用 SSE 帧驱动一个纯 reducer，
> 覆盖聊天、进度、动作、历史恢复和断流重连。
>
> 断流没有做 token 级续传，而是 **C2-lite：保存先于展示 + 同一
> `client_message_id` 回放/重试 + 历史对账**。
> 用户看到的回答一定已经落库；断流后先查服务端有没有回答，
> 有就整段替换本地草稿，没有就保留草稿并给「继续」。
>
> 验收结果：后端 **1084 passed / 10 skipped**，前端 **11 个文件 / 78 条测试**，
> 四场景 API、浏览器断流演练、三张界面截图通过；2 分钟录屏待补。
>
> 最重要的取舍：**前端不猜服务端状态，断流后先对账再决定是否重试。**」

---

## 1. 架构：SSE 帧 → reducer → UI

```
POST /api/chat
      │
      ▼
 ack → route → progress* → delta → citations → actions → done
                                      ↘ error
      │
      ▼
 sendChat / drainFrames        client/src/lib/sse.ts
      │  frame
      ▼
 turnReducer                   client/src/state/turn.ts
      │  TurnState
      ▼
 ChatWindow / ProgressPanel / ActionBar
```

### 1.1 为什么用 `fetch + ReadableStream`，不用 `EventSource`

- `EventSource` 只能发 GET，不能带 POST body，也不能自定义 `X-Trace-Id`；
- 对话需要 `message / conversation_id / client_message_id / selected_action`；
- 所以用 `fetch` 拿 `response.body`，自己按 `\n\n` 切 SSE 帧。

`drainFrames` 处理了四个边界：

| 边界 | 处理 |
|---|---|
| 分块到达 | 保留半帧，下一块拼上再解析 |
| CRLF | 统一替换成 `\n` |
| 多行 `data:` | 拼成一条 JSON |
| 坏 JSON / keepalive | 跳过这一帧，不崩整条流 |

### 1.2 帧的语义

| 帧 | 作用 |
|---|---|
| `ack` | 返回 `conversation_id` / `user_message_id` |
| `route` | 分类完成就发，前端可以立刻显示进度 |
| `progress` | `checking_order` / `amount_ready` / `waiting_confirm` 等 |
| `delta` | **整段回答**，不是 token |
| `citations` | 引用 id + 详情（`section_path/text/source`） |
| `actions` | 订单/规格选项、继续流程、转人工 |
| `done` | 助手消息已落库，返回 `message_id` |
| `error` | 传输层/业务错误，替换后续帧 |

**要主动说**：这不是 token 级流式。`delta` 是整段一帧，进度靠
`progress` 帧单独表达。这样做换来了简单、可回放、无 token 缓冲；
代价是首字延迟不如 token 流。

### 1.3 为什么用 reducer

`turnReducer` 是纯函数：输入 `TurnState + ChatFrame`，输出新的 `TurnState`。
副作用（fetch、abort、localStorage、路由）都在 hooks 里。

好处：

- 帧乱序/重复/未知 stage 都能在 reducer 里防御；
- 单测不需要打网络；
- `retry`、`interrupt`、`history` 是显式动作，不靠零散 `setState`。

一个具体防御：`progress` 的 `waiting` 是终态，后续同 stage 的更新不再覆盖它。
否则「等待您确认」可能被后来的 `running` 帧重新点亮成转圈。

### 1.4 代码地图

| 层 | 文件 | 职责 |
|---|---|---|
| SSE 传输 | `client/src/lib/sse.ts` | POST、切帧、坏帧跳过 |
| 状态机 | `client/src/state/turn.ts` | 帧 → 消息/进度/错误；重试丢草稿 |
| 会话副作用 | `client/src/hooks/useChatSession.ts` | 发送、断流对账、重试、停止、切换 |
| 历史恢复 | `client/src/state/reconnect.ts` | 历史 → `TurnState`；按 `reply_to` 对账 |
| 会话列表 | `client/src/hooks/useConversations.ts` | 列表、bootstrap、加载、软删除 |
| 布局 | `client/src/App.tsx` | 组合；桌面三栏 / 小屏抽屉 |

---

## 2. C2-lite：断流恢复

### 2.1 问题是什么

最危险的时序不是「请求没发出去」，而是：

```text
后端已经保存 assistant + flow
        ↓
delta 发到一半 / 用户网络断了
        ↓
前端没收到 done
```

如果前端直接重跑，可能写出第二条回答；如果前端只保留草稿，
服务端和页面就会不一致。C2-lite 解决的是这个窗口。

### 2.2 两条路径

**路径 A：服务端已经回答过**

```text
断流
  → GET /api/conversations/{id}/messages
  → 找 assistant.reply_to_client_message_id == 本地 client_message_id
  → 找到且 status != error
  → 用历史重建整个 TurnState（回答、引用、动作、金额、进度）
```

**路径 B：服务端还没回答**

```text
断流
  → 历史里找不到对应 reply
  → 保留本地草稿
  → 显示「连接中断，请点击继续」
  → 用户点继续：POST 同一个 client_message_id
       ├─ 后端已保存 → _replay_frames 回放，不跑图、不再写库
       └─ 后端没保存 → 正常跑一轮
```

关键点：**重试复用同一个 `client_message_id`，不是每次重试新生成一个。**

### 2.3 数据库怎么保证只有一条

`messages` 表有两个部分唯一索引：

| 索引 | 约束 |
|---|---|
| `uk_msg_idem` | 同一会话内 `client_message_id` 唯一（只约束非 NULL） |
| `uk_msg_reply` | 同一会话内 `reply_to_client_message_id` 唯一（只约束非 NULL） |

- 用户消息写 `client_message_id`；
- 助手消息写 `reply_to_client_message_id`，指向它回答的那条用户消息；
- 错误消息不写 `reply_to_client_message_id`，所以重试不会回放错误结果；
- 并发重试撞唯一索引时，取回已存在的那条，不再写第二条。

**为什么用部分索引**：普通唯一索引下多条 NULL 也会冲突；部分索引只约束
带幂等键的行，旧消息/工具消息可以继续为 NULL。

### 2.4 为什么不做 Last-Event-ID / token 缓冲

| 方案 | 为什么没选 |
|---|---|
| Last-Event-ID | 需要服务端缓存每个 token 事件；我们 `delta` 是整段，没有 token 流可续 |
| 前端 token 缓冲 | 断流时缓冲可能不完整；最终还是要以服务端已存回答为准 |
| WebSocket | 需求是单向推送；SSE + POST 更简单，断线语义也够用 |

C2-lite 的判断是：**最终回答已经落库，历史对账比补 token 更可靠。**
Last-Event-ID 解决「流断在哪」，历史对账解决「服务端到底有没有答完」。

### 2.5 刷新后为什么还能继续

刷新后没有内存状态，只剩历史接口。`buildHistoryState` 看最后一条消息：

```ts
const lastItem = history.items[history.items.length - 1];
const interrupted = lastItem?.role === "user";
```

- 最后一条是 user → `interrupted=true`；
- error 文案：「上次请求未完成，请点击继续」；
- `lastClientMessageId` 从最后一条 user 消息恢复；
- 点继续时走路径 A/B，仍然复用同一个 id。

---

## 3. 保存先于展示：fail-closed

后端顺序是：

```text
输出守卫 guard_answer
  → save_assistant_message + apply_flow_decision（同一事务）
  → 成功后才发 progress/delta/citations/actions/done
  → 保存失败只发 error，不发 delta
```

为什么：

- 如果先发 delta 再落库，用户看到了回答，但历史里没有；
- 刷新后回答消失，或者重试写出第二条；
- 流程状态（`active_flow/pending_question/draft`）和回答必须一起写，
  不能出现「消息写了、流程没写」的半截状态。

`turn:persist` span 记录持久化耗时；失败时 `persisted=false`，
不会伪装成成功。

**面试里可以主动说**：这是「用户看到的回答一定已落库」的不变式，
代价是 delta 要等落库完成，首字会晚一点。

---

## 4. 进度、动作与“实际转人工”

### 4.1 进度不是前端猜的

`ProgressPanel` 的步骤来自后端 `progress` 帧和助手 `meta.signals`：

- `checking_order`：`validate` / `query_data` 的 update + 累积 state；
- `amount_ready`：金额只来自 `refund_amount`，且必须 `eligible + 合规通过`；
- `waiting_confirm`：等用户确认；
- `handoff`：转人工；
- `waiting_info`：等用户补槽位。

前端只做展示映射，不从自然语言里正则抠金额/状态。

### 4.2 实际转人工 vs 只是给按钮

这是 Ch08 修过的一个语义问题：

| 场景 | 是否算已转接 |
|---|---|
| `route=human` | 是 |
| `rules` 的 `transfer()` 带 `transfer_reason` | 是 |
| `fallback` 的 `transfer_human` 动作 | **否**，只是提供一个按钮 |

后端把 `signals.handoff` 写进助手 meta；前端 `handoffStarted` 只认实际转接。
否则用户只是看到一个「转人工」按钮，进度却显示「已转接人工，等待接入」。

### 4.3 多意图澄清

`classify_intent` 写 `multi_intent`；`route_by_intent` 看到它就强制走
`fallback`；`fallback_agent` 直接返回澄清话术，**不进入 ReAct**。

为什么：多意图时如果只答一个，用户会以为另一个被忽略；直接让模型自由发挥
又可能编造。澄清是一轮低成本、可预期的兜底。

### 4.4 等待态用时钟，不用转圈

`waiting_confirm / waiting_info / handoff` 都是 `waiting`，不是 `running`。
Spinner 表示系统正在处理；等用户操作应该用时钟。

这也是一个面试点：**状态语义要分开，不能把「等用户」和「系统在跑」都映射成
`process`。** 否则用户会一直等一个永远不会结束的转圈。

---

## 5. 历史恢复与会话 UX

### 5.1 历史接口的白名单

`GET /api/conversations/{id}/messages` 只返回 `user / assistant`：

- 不返回 `tool` 消息、`tool_calls`、`trace_id`；
- 不返回 `draft`、`order_snapshot`、完整 `refund_judgement`；
- 不返回置信度、规则 id 等内部信号；
- 不存在 / 不属于本人 / 已软删统一 404，不泄露存在性。

### 5.2 软删除

`conversations.deleted_at` 只打标记，不物理删：

- 列表只返回未删除会话；
- 消息还在，便于审计/排障；
- 删除会话是 204，删别人的/已删的统一 404。

### 5.3 启动时选哪个会话

优先级：

```text
URL ?c=  >  localStorage lastConversationId  >  列表第一条
```

如果 URL 指向的会话 404，前端清掉 localStorage，不阻塞新对话。

### 5.4 生成中切换会话

直接切会让当前回复停在半空，用户也不知道发生了什么。现在的交互：

- 生成中点另一个会话/新对话 → 弹确认；
- 「停止并继续」→ abort 当前流，再切换；
- 「继续等待」→ 不切；
- 新会话在 `ack` 时就刷新列表，不等 `done`，避免“新会话消失”。

### 5.5 小屏用抽屉，不做上下堆叠

`Grid.useBreakpoint().lg === false` 时：

- 隐藏左侧会话栏；
- 头部出现「会话」按钮；
- 打开 antd `Drawer` 复用同一个 `ConversationList`；
- CSS 断点用 **991px**，和 antd `lg` 对齐。

之前用上下堆叠，会话列表占掉上半屏，聊天区被压得很小；抽屉把主对话还给用户。

### 5.6 C 端不展示内部状态

页面不展示 `route`、在线状态、内部意图名。这些是调试/验收信息，
C 端用户只需要知道「客服在不在处理、下一步做什么」。

---

## 6. 四个踩坑故事（STAR）

### 6.1 「等待您确认」一直转圈

- **S**：退款回答已经结束，进度条最后一步还在转；
- **T**：区分「系统在跑」和「等用户操作」；
- **A**：`statusOf` 不再把 `waiting` 折叠成 `process`；新增 `waiting` 状态；
  Steps 用 `ClockCircleOutlined` 覆盖当前态，内联进度用琥珀色时钟；
- **R**：等待确认不再像卡死；新增 2 条测试断言没有 `anticon-loading`。

**可讲**：「Spinner 是系统语义，不是装饰；等待用户必须用另一个状态。」

### 6.2 用户打字「转人工」没反应

- **S**：点转人工按钮正常，但用户直接输入「转人工」却走普通分类/兜底；
- **T**：结构化动作和自然语言入口要统一；
- **A**：后端加 `_TRANSFER_PHRASES` 短路，和 `selected_action.type=transfer_human`
  走同一出口；同时区分「实际转接」和「fallback 只给按钮」；
- **R**：打字和点按钮都能转；进度不会把「提供按钮」误报成「已转接」。

**可讲**：「同一业务意图有多个入口时，入口可以不同，出口必须收敛。」

### 6.3 生成中切换会话，新会话消失

- **S**：AI 正在生成时切到别的会话，新会话从左侧列表消失；
- **T**：列表刷新时机不能只绑在 `done`；
- **A**：`ack` 到达就刷新列表；切换/新对话前先确认停止；切换后刷新；
- **R**：新会话在生成开始时就出现，切换不再丢。

**可讲**：「UI 状态的生命周期要跟服务端事实走，不能跟流结束走。」

### 6.4 刷新后没有「继续」按钮

- **S**：断流后刷新，历史里只有 user 消息，页面看起来停住；
- **T**：刷新后也要能恢复未完成的一轮；
- **A**：`buildHistoryState` 判断最后一条是不是 user，恢复
  `interrupted + lastClientMessageId`；
- **R**：刷新后显示「上次请求未完成，请点击继续」；点继续用同一 id 对账。

**可讲**：「断流恢复不是只处理当前页面，刷新是同一个问题的另一个入口。」

### 6.5 其他小坑（可一句话带过）

| 问题 | 修法 |
|---|---|
| 引用浮层盖住整页 | `maxWidth: 420 / maxHeight: 320`，内容清理 Markdown |
| 「其他原因」只填「其他」 | 前端拒绝通用词；后端要求 `reason_text` |
| 原因文本 `111111` 被当成新订单 | `intake` 保护已有 `order_id`，不覆盖 |
| 欢迎页重复聊天头部 | welcome 状态隐藏 `.chat-header` |
| 小屏列表占满上半屏 | `<992px` 改抽屉 |

---

## 7. 追问清单

| 问题 | 答题要点 |
|---|---|
| 为什么不用 EventSource？ | 只能 GET、不能带 body/自定义 header；对话要 POST + trace id |
| 为什么用 reducer？ | 纯函数、可单测、帧防御集中；副作用在 hooks |
| 为什么 delta 不是 token？ | 产品要的是最终回答和进度；整段落库后可回放，省 token 缓冲 |
| 断流后怎么判断服务端有没有答完？ | 查历史，按 `reply_to_client_message_id == client_message_id` 找 |
| 找到已存回答后怎么处理本地草稿？ | 整段替换成历史重建的 `TurnState`，保留服务端最终措辞/动作/金额 |
| 没找到呢？ | 保留草稿，显示「继续」；点继续复用同一 id 重试 |
| 为什么重试不会写出两条？ | `uk_msg_reply` 唯一部分索引 + 后端先查回放 |
| 为什么用户消息也要唯一键？ | `uk_msg_idem` 防同一请求重复写 user 消息 |
| 为什么错误消息不写 reply_to？ | 否则重试会回放错误结果；错误不是成功回答 |
| 为什么先落库再发 delta？ | 用户看到的回答一定已落库；失败只发 error，不发半截回答 |
| 为什么不做 Last-Event-ID？ | 没有 token 缓冲；历史对账解决「服务端有没有答完」 |
| 原请求还在跑时点继续会怎样？ | 会话锁返回 429 busy；前端提示并冷却 5 秒，不自动重试 |
| `ack` 丢了怎么办？ | 前端没有 conversation_id，继续会新建会话；这是已知边界 |
| 进度为什么不在前端算？ | 状态/金额来自后端图与工具；前端正则抠金额会漂移 |
| 为什么 fallback 的转人工不算已转接？ | 它只是提供按钮，用户还没点；只有 `route=human` 或 `transfer_reason` 才算 |
| 历史接口为什么不返回 tool/draft？ | C 端最小暴露；内部字段走白名单，防泄露 |
| 小屏为什么用抽屉？ | 上下堆叠会压扁聊天区；抽屉把主对话还回来 |
| 前端测试覆盖了什么？ | reducer、SSE 切帧、历史对账、进度、动作、会话列表；没有 Playwright E2E |
| 为什么构建 870 kB？ | antd 全量组件；Ch08 不做 code split，P9 交付前再评估 |
| 下一步做什么？ | 2 分钟录屏、Playwright E2E、code split、真实鉴权、多实例锁 |

---

## 8. 不要说的话

| 别说 | 为什么 |
|---|---|
| 「实现了 token 级流式」 | `delta` 是整段；进度靠 `progress` 帧 |
| 「断流靠 Last-Event-ID」 | 实际是 C2-lite 历史对账 + 同 id 重放 |
| 「用了 WebSocket」 | 是 POST + SSE |
| 「有工单 / 坐席队列」 | 转人工只给固定话术和动作，不建工单 |
| 「生产可用 / 高并发」 | 本地可运行；单进程锁，无生产鉴权/压测 |
| 「界面验收完成 / 有演示录屏」 | 三张截图已补；2 分钟录屏仍待补 |
| 「前端测试覆盖所有 UI」 | 78 条单测/组件测；没有 E2E |
| 「Ch08 做了数据飞轮」 | 飞轮是可选 Ch09，只有 spec/plan |
| 「多实例安全」 | 会话锁是单进程；多 worker 要换 Redis 等 |

---

## 9. 数字速查

| 数字 | 含义 | 出处 |
|---|---:|---|
| 1084 / 10 | 后端测试 passed / skipped | `ch08-acceptance.md` |
| 11 / 78 | 前端测试文件 / 用例 | `ch08-acceptance.md` |
| 870.53 kB / 274.55 kB | dist JS / gzip；antd 超过 500 kB 警告 | `ch08-acceptance.md` |
| 8 | SSE 帧类型：ack/route/progress/delta/citations/actions/done/error | `app/api/chat.py` |
| 4 | 演示场景：退款 / 转人工 / 知识 / 离开再回来 | `ch08-acceptance.md` |
| 3 | 已补界面截图 | `docs/assets/ch08/` |
| 2 min | 演示录屏，待补 | `00-tasks.md` P9-3 |
| 1 + 1 | 一轮对话的 user + assistant 上限（唯一约束） | `app/db/models.py` |
| 0 | 重复用户 / 重复助手 / 孤儿回复检查结果 | `ch08-acceptance.md` §4.3 |
| 57 / 253 / 254 | 断流演练会话 / user 消息 / assistant 消息 | `ch08-acceptance.md` §4.3 |
| 500 | 单条消息最大字符数 | `app/api/chat.py` |
| 20 | 会话列表最多返回条数 | `app/api/conversations.py` |
| 5s | 429 busy 后的前端冷却 | `useChatSession.ts` |
| 991px | 小屏抽屉断点（对齐 antd lg） | `styles.css` |
| 420×320 | 引用浮层最大宽高 | `MessageBubble.tsx` |

---

## 10. 现场演示脚本（约 5 分钟）

### 10.1 正常退款

1. 新会话输入「订单1002 不想要了」；
2. 看进度：`已核对订单 → 已算出金额 ¥1249.00 → 等待您确认`；
3. 指出金额只出现一次，等待态是时钟不是转圈。

### 10.2 知识问答与引用

1. 输入「退货政策是什么」；
2. 回答里出现 `[1][2]`；
3. 悬停/点击角标，展示来源浮层（`faq.md`）。

### 10.3 转人工

1. 输入「我要投诉你们的客服」；
2. 看 `route=human` 的固定话术；
3. 说明「不建工单，只给出口和动作」。

### 10.4 断流恢复

1. 发一轮，生成中停止后端；
2. 页面出现「继续」；
3. 恢复后端，点继续；
4. 说明同一 `client_message_id`、只有一条回答、刷新后还能继续。

> 演示时只说三件事：**走了哪个出口、为什么、结果对不对。**
> 不要边跑边翻代码；代码位置留给追问。
