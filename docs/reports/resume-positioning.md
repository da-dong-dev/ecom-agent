# 简历措辞与实现状态对照

> **用途**：防止简历写了还没做的东西。每条措辞标注【可用时点】和【面试会被追问什么】。
> **规则**：只有代码 + 验收都完成的描述才允许进简历。答不上来三层的词，一个都不写。
> 更新于 2026-11-18（Ch01–Ch08 已验收；Ch09 离线飞轮闭环完成；Ch08 录屏待补）。

## 〇、当前状态（2026-11-18）

- **Ch01–Ch07**：已验收；Ch07 的 44 条评测、确定性回归、阈值曲线、退款困难组都有报告。
- **Ch08 后端契约**：已完成（迁移 / 回放 / 历史接口 / progress 帧 / 转人工短路）。
- **Ch08 前端**：聊天窗、进度面板、动作卡片、历史恢复、会话列表、断流重连已完成；
  四场景 API / 回归 / 断流浏览器演练完成（Task 0–12），**三张界面截图已补；2 分钟录屏待补**。
- 因此：Ch08 可以写“已实现、四场景 API 与断流恢复已验证”；有录屏前**不要写“界面验收完成”**。
- **Ch09 离线模拟飞轮**：落池 → 标准化/查重 → 人工审核/审核 UI → FAQ 写回 → Chroma 检索闭环完成；
  12 条 badcase hit@3 **0.50 → 1.00**、citation **0/12 → 12/12**，44 条回归 **44/44**。
  边界：没有真实用户流量，12 条答案是合成演示数据；**不要写“线上飞轮/自动写回”**。
  验收见 [ch09-flywheel-acceptance.md](./ch09-flywheel-acceptance.md)。

## 一、分阶段措辞

### 阶段 1 · 现在可用（Ch01 已完成）

```
ecom-agent · 电商售后客服 Agent（进行中）
Python / SQLAlchemy / FastAPI / SQLite

· 数据地基：10 张表（6 系统 + 4 业务），部分唯一索引（WHERE ... IS NOT NULL）
  实现消息幂等；PRAGMA 外键 + WAL，schema.sql 与 ORM 自动比对防漂移。

· 越权防护：user_id 作为 keyword-only 必填参数，归属校验下沉到 SQL 层；
  不存在与无权限返回同一异常，避免订单枚举。

· 合成数据：6 SKU / 20 订单 / 5 券，覆盖 6 种订单状态与 4 类物流形态。
```

**为什么可以写**：Ch01 的 8 个 commit 全部落地，可随时演示。
（当时 30 条测试；当前全量为 **1060 passed, 14 skipped**——
简历上不必写测试条数，见「三、当前红线」。）

### 阶段 2 · Ch03 已完成（历史阶段，仍可写）

```
· 金额可复现：C 出口 9 状态确定性流水线，退费计算为纯函数
  （无 IO / 无时钟 / 无随机）；合规节点做红线检查 + 规则代码独立复算，
  模型与代码分歧即转人工；手算 10 份合同对拍 10/10 一致。

· 判定评测：30 条人工标注 gold set（简单组 20 / 困难组 10），
  简单组模型 20/20 不退化；困难组模型 7/10 vs 规则 2/10；
  错放率 0%，端到端 P95 4.72s。
```

**可用条件**：已满足。证据在 `docs/reports/ch03-acceptance.md` 与 `refund-eval.md`。

**面试会被追问**：
- "困难组才 10 条？" → 答：n=10 的 95% CI 约 [40%, 89%]，只能支撑方向性结论；
  扩充计划在 Ch07（分层抽样 + 留盲测 + 报 CI）。
- "为什么模型 7/10 还敢上线？" → 答：流水线有 `compliance` 兜底，分歧必转人工；
  错放率 0%，没有出现"该拒的判成可退"。

### 阶段 3 · Ch05 完成后，换标题（历史阶段，仍可写）

```
ecom-agent · 电商售后客服 Agent —— 确定性 Workflow + 只读 ReAct 混合架构
Python / LangGraph / FastAPI / SQLite / Chroma

· 主流程：LangGraph 主图 + 5 个出口（knowledge / rules 两个子图 +
  data / human / fallback 节点），12 类意图查表路由（无模型决策），
  各出口独立收尾、可单测。

· 金额可复现：C 出口 9 状态确定性流水线，退费计算为纯函数
  （无 IO / 无时钟 / 无随机）；合规节点做红线 + 规则代码独立复算，
  分歧即转人工；手算 10 份合同对拍 10/10 一致。

· 判定评测：30 条人工标注 gold set（简单组 20 / 困难组 10），
  简单组 20/20 不退化；困难组模型 7/10 vs 规则 2/10；错放率 0%。

· 多轮上下文：消息落库 + 滑动窗口 + 滚动摘要；跨轮用
  `active_flow / pending_question / draft` 三字段恢复，槽位命中后短路分类；
  改话题 park、30 分钟失效、会话级锁串行。

· 兜底长尾：兜底出口引入只读 ReAct Agent（4 个白名单工具、4 步封顶、
  调用链落 trace），承接未预定义意图；写操作一律不进工具集。

· 工程约束：工具侧注入 user_id + SQL 层归属过滤，越权与不存在同异常（防枚举）；
  client_message_id 幂等去重；每节点一行 trace_span。
```

> 评测部分已被阶段 4 取代：优先写 44 条 / run-03 / held-out；30 条 gold set 只作为 Ch03 历史结果。

**为什么这么定标题**：`[项目名] · [场景] —— [架构一句话]`。前 12 字内出现 Agent / Workflow，
HR 扫读能命中关键词；「混合架构」这四个字本身就是取舍能力的信号。

### 阶段 4 · Ch07 已验收；Ch08 前端功能完成（2026-11-17）

```
· 评测：44 条 / 7 桶端到端评测；确定性回归 44/44；
  run-03 correctness 0.9977、recall 1.00、faithfulness 1.00；
  退款困难组 dev 10/10、held-out 15/15（模型），流水线 8/10、13/15；
  held-out 不是严格盲测，报告里注明。

· 后端契约（Ch08）：助手消息 reply_to_client_message_id + 唯一部分索引；
  同一 client_message_id 回放；会话/历史接口；LangGraph 子图 progress 帧；
  转人工结构化入口；保存失败 fail-closed。

· 前端（Ch08 Task 0–12）：React + Vite + TypeScript；SSE 帧 reducer；
  进度面板（核对中/已算出/待确认）；订单/规格动作卡片 + 转人工；
  刷新后历史恢复与会话列表；断流恢复（先对账再重试）；antd 组件 + 自定义聊天气泡；
  78 条前端单测。
```

**可用时点**：

- Ch07 的指标**现在可以写**，但必须带上下文：44 条、run-03、held-out 非严格盲测。
- Ch08 前端**可以写“已实现、四场景 API 与断流恢复已验证”**；有录屏前
  **不要写“界面验收完成”**。

---

## 二、逐条可追问点对照

| 简历措辞 | 依赖的代码 / 验收 | 现在有吗 | 会被追问什么 |
|---|---|---|---|
| 10 张表、部分唯一索引 | `app/db/models.py`、`schema.sql` | ✅ | 为什么用部分索引？多个 NULL 会怎样？ |
| PRAGMA 外键 + WAL | `app/db/session.py` | ✅ | 为什么挂在 connect 事件而不是启动时？ |
| schema.sql 与 ORM 自动比对 | `scripts/init_db.py` | ✅ | 为什么两边都写？漂移怎么发现？ |
| user_id keyword-only | `app/db/repo_order.py`、`errors.py` | ✅ | 为什么可选参数不行？为什么越权和不存在同异常？ |
| 6 SKU / 20 订单 / 5 券 | `scripts/seed.py`、`tests/db/test_seed.py` | ✅ | 为什么每人 3–5 单？为什么必须有一单超 7 天？ |
| 12 类意图查表路由 | `app/graph/routing.py`（Ch02） | ✅ | 为什么不用模型决策路由？ |
| 9 状态确定性流水线 | `app/graph/rules_exit.py` | ✅ | 为什么不 ReAct？need_info 怎么恢复？ |
| 金额纯函数 + 对拍 10/10 | `app/rules/refund.py`、`refund_10.json` | ✅ | 哪 10 份？边界怎么选？ |
| 合规 = 红线 + 独立复算 | `app/graph/rules_exit.py` · `compliance` | ✅ | 为什么不以代码为准？分歧率怎么统计？ |
| 44 条 / 7 桶端到端评测 | `data/eval/eval_v1.yaml`、`docs/reports/ch07-acceptance.md` | ✅ | 44 条怎么分桶？deterministic 44/44 是什么？ |
| run-03 指标（correctness 0.9977 / recall 1.00） | `docs/reports/run-03.md` | ✅ | 为什么 relevance 0.89 而 correctness 0.9977？成本多少？ |
| 退款困难组 dev 10 + held-out 15 + CI | `docs/reports/refund-hard.md`、`ch07-acceptance.md` | ✅ | 模型 10/10、15/15 为什么流水线只有 8/10、13/15？held-out 为什么不是盲测？ |
| 证据闸阈值扫描 | `docs/reports/gate-thresholds.md`、`gate-thresholds.csv` | ✅ | 0.55 怎么选？双零区间是什么？ |
| 后端契约：回放 / 历史 / progress / 转人工 | `app/api/chat.py`、`app/api/conversations.py`、`app/core/progress.py` | ✅ | 为什么要 reply_to 列？progress 为什么从子图取？保存失败为什么 fail-closed？ |
| 前端 SSE / reducer / 进度 / 动作 / 历史 / 断流 | `client/src/lib/sse.ts`、`state/turn.ts`、`components/ProgressPanel.tsx`、`ActionBar.tsx`、`state/reconnect.ts` | ✅（Task 0–12；断流演练已过；三张截图已补；录屏待补） | 为什么用 reducer？历史怎么恢复？断流为什么先对账再重试？ |
| 四类转人工集成测试 | `tests/graph/rules/test_transfer_paths.py` | ✅ | 哪四类？打桩了哪些依赖？ |
| 评测样本量的统计意识 | `refund-hard.md`、`ch07-acceptance.md`（dev 10 + held-out 15） | ✅ | held-out 15 条够吗？CI 多宽？为什么不是盲测？ |
| 只读 ReAct + 4 步封顶 | `app/core/react.py`、`fallback_agent.py` | ✅ | 为什么只读？步数为什么是 4？ |
| 多轮：三字段 + 窗口 + park + 锁 | `app/core/session_state.py`、`history.py`、`lock.py` | ✅ | 为什么不用 checkpointer？ |
| 幂等去重 | `repo_message.append_message` | ✅ | 并发重试怎么办？ |
| trace_span 每节点一行 | `app/core/tracer.py`、子图 `_traced` | ✅ | 怎么还原调用树？ |

---

## 三、当前红线（这些词现在不准写）

| 词 | 为什么 | 什么时候能写 |
|---|---|---|
| 不带上下文的「准确率 99.77%」 | 指标必须带数据集 / 运行 / 样本量；held-out 非严格盲测 | 写「44 条 run-03 correctness 0.9977」可以；单独写「准确率 99.77%」不行 |
| 任何准确率、延迟、成本数字 | 必须有 `docs/reports/` 的运行记录 | 有记录才写，并注明样本量 |
| 界面验收完成 / 有演示录屏 | 四场景 API + 断流演练 + 三张截图已验证；录屏未补 | 录屏补齐后 |
| 高并发 / 支撑 QPS | 无压测 | 不做压测就别写 |
| 线上 / 生产环境 | 从未部署 | 保持「本地可运行」的表述 |

> **可以写**：混合检索 / RRF / hit@3（Ch02，见 `retrieval-eval.md`）、
> 端到端 P95 4.72s（n=5，见 `ch03-acceptance.md`）、
> 多轮退款轮中位 3.62s / ReAct 中位 2.59s（n=5，见 `ch05-acceptance.md`）、
> 44 条评测 + deterministic 44/44 + run-03 指标（见 `ch07-acceptance.md` / `run-03.md`）。

---

## 四、面试展开材料

简历上的每一句都可能被追问。逐题的答法、依据代码位置、不能说的话，见：

- [面试材料索引](../interview/README.md)：各章共用结构与跨章主线
- [项目复习手册](./project-review.md)：Ch02/03/05 主线、四格卡、决策地图
- [Ch01 数据地基](../interview/ch01-data-foundation.md)：双写 DDL、幂等、越权
- [Ch02 检索与骨架](../interview/ch02-retrieval-skeleton.md)：查表路由、检索选型、证据闸
- [Ch03 退款流水线](../interview/ch03-refund.md)：架构取舍、评测设计、踩坑故事
- [Ch04 数据出口](../interview/ch04-data-exit.md)、[Ch05 多轮](../interview/ch05-multi-turn.md)
- [Ch07 导读](./ch07-overview.md)、[Ch07 验收](./ch07-acceptance.md)：44 条评测与已知边界
- [Ch07 面试要点](../interview/ch07-eval.md)：双回路、阈值、退款困难组、judge 方差
- [Ch08 验收](./ch08-acceptance.md)、[Ch08 面试要点](../interview/ch08-frontend.md)：
  SSE 帧 reducer、进度/动作、历史恢复、C2-lite 断流
- [Ch09 验收](./ch09-flywheel-acceptance.md)、[Ch09 面试要点](../interview/ch09-flywheel.md)：
  落池、标准化/查重、审核、FAQ 写回、补库前后评测

## 五、措辞纪律

1. **数字必须有出处**：每个百分比、每个「N/N」都要能在 `docs/reports/` 找到运行记录。
2. **「进行中」是加分**：它说明有节奏，且面试官会追问下一步——那正是讲架构取舍的机会。
3. **不写没实现的框架名**：简历里出现 LangGraph / Chroma，代码里就必须有。
4. **标题不超过 32 字**，`[项目名] · [场景] —— [架构]` 三段式。
