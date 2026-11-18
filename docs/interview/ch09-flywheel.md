# Ch09 面试要点：离线数据飞轮

> 用途：这一章能讲什么、会被追问什么。每题给「结论 + 依据 + 出处」。
> 证据来源：[ch09-flywheel-acceptance.md](../reports/ch09-flywheel-acceptance.md)、
> [ch09-flywheel-changes.md](../reports/ch09-flywheel-changes.md)、
> [ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md)
> 状态：Ch09 Task 0–8 已完成（2026-11-18）；**离线模拟，无真实用户流量**。

---

## 0. 30 秒版本

> 「Ch09 不是加一个审核页，而是把已有的 `low_conf_questions` 和 `faq`
> 接成一条可验证的数据管线：
> **落池 → 标准化/查重 → 人工审核 → FAQ 写回 → Chroma 检索 → 补库前后评测**。
>
> 核心原则：**不自动写回**，所有 FAQ 必须人工通过；
> **只有向量化成功才算 `written_back`**，不允许“数据库说写回成功、检索却查不到”。
>
> 结果：12 条 badcase hit@3 **0.50 → 1.00**、citation **0/12 → 12/12**、
> 库外题拒答率 **0.70 → 0.80**，Ch07 44 条确定性回归 **44/44**。
>
> 边界：只有 12 条合成 badcase + 10 条库外题，是离线演示，
> 不是线上飞轮，也不是模型自动学习。」

---

## 1. 问题：池子只进不出

Ch09 开始前的真实状态：

| 能力 | 状态 |
|---|---|
| 低置信问题落池 | ✅ `low_conf_questions` 已有 |
| 标准化 / 查重 | ❌ 没有 |
| 审核入口 | ❌ 没有 |
| FAQ 表 | ⚠️ 建了，但没有写入路径 |
| FAQ 参与检索 | ❌ `build_kb.py` 只读静态 Markdown |
| 补库前后评测 | ❌ 没有 |

所以问题不是“再加一个页面”，而是：

> 池子越积越多，FAQ 表永远空着，没人能证明补库有没有用。

### 边界（主动说）

| 不做 | 原因 |
|---|---|
| 自动生成答案并写库 | 没有人工确认就是模型自己教自己，错误会累积 |
| 实时入队 / 实时写回 | 成本高，批处理足够 |
| 线上用户反馈闭环 | 没有真实流量、登录和反馈入口 |
| 模型微调 | 飞轮补的是知识，不是参数 |
| 多级审批 / 权限 | 单人本地项目，先不做 |

---

## 2. 架构与关键决策

```
① 入口
   知识弱证据（已有） / 点踩 / 规则冲突
        ↓
② low_conf_questions
   question + source + reason + retrieved 三态
        ↓
③ scripts/flywheel.py
   LLM 标准化 + embedding 查重
        ↓
④ 人工审核 scripts/review.py / #review
   通过 → faq(pending)
   驳回 → rejected（终态）
        ↓
⑤ build_kb
   静态语料 + FAQ chunks → Chroma
   faq.sync_status=synced
   low_conf.status=written_back
        ↓
⑥ 评测
   同一批 badcase before/after + 库外拒答 + 44 条回归
```

| 决策 | 选择 | 理由 |
|---|---|---|
| 队列表 | 复用 `low_conf_questions` + `faq` | 现有字段已够，新建 `review_queue` 会重复状态 |
| 处理方式 | 批处理，不实时 | 可重跑、不增加在线延迟 |
| 写回 | 必须人工 approve | 防止模型错误进入知识库 |
| 写回位置 | `faq` 表，再合并进 Chroma | 可回滚、可整表停用，不污染 Markdown |
| 查重 | LLM 标准化 + embedding 0.9 二次校验 | LLM 会漏判/误判，embedding 兜底 |
| 合并时机 | build 时合并 | 检索链路不变，一次查询即可 |
| 入口 | 先 CLI/API，UI 可选 | 先验证闭环，不把飞轮做成前端练习 |

---

## 3. 状态机：只有 pending canonical 能审核

`low_conf_questions.status` 有 5 个值：

| 状态 | 含义 | 终态 |
|---|---|---|
| `pending` | 待处理 / 待审核 | 否 |
| `merged` | 同义原话已合并到 canonical | 是 |
| `approved` | 审核通过，等 KB 同步 | 否 |
| `written_back` | FAQ 已写、向量化成功 | 是 |
| `rejected` | 审核驳回 | 是 |

`pending` 有两种：

```text
pending + normalized IS NULL
    = 原始问题，等批处理

pending + normalized IS NOT NULL + merged_into IS NULL
    = canonical，等人工审核
```

审核对象必须同时满足：

```sql
status = 'pending'
AND normalized IS NOT NULL
AND merged_into IS NULL
```

为什么：

- `normalized` 为空 → 还没标准化，无法写 FAQ；
- `merged_into` 非空 → 它是同义原话，不是审核对象；
- 其他状态 → 已经做过决定，不能再流转。

### 状态流转

```text
pending(raw)
  ├─ 标准化后新建 → pending(canonical)
  └─ 命中已有 → merged

pending(canonical)
  ├─ approve → approved → 同步成功 → written_back
  └─ reject  → rejected（终态）

approved
  └─ 同步失败 → 保持 approved，FAQ pending，可 retry
```

**关键规则**：

- `rejected` 是终态；同义问题后续只累加 `occurrence`，不复活；
- `written_back` 是终态；同一 canonical 不会再次写 FAQ；
- 只有向量化成功才允许 `written_back`，否则就是假成功。

---

## 4. 标准化与查重

### 4.1 LLM 做什么

输入用户原话 + 已标准化候选：

```text
[12] 价保规则是什么（出现 3 次）
[15] 支持以旧换新吗（出现 1 次）
```

输出：

```json
{
  "normalized_question": "价保规则是什么",
  "matched_id": 12,
  "suggested_answer": "签收后 7 天内同款降价可申请补差。"
}
```

约束：

- `matched_id` **只能**来自候选，不能编造；
- 不确定就填 `null`；
- 去情绪、口语、寒暄；
- 不臆造政策数字/时效/金额。

### 4.2 embedding 二次校验

LLM 说命中不代表真的命中：

```text
embedding 相似度 >= 0.9 → merge
embedding 相似度 <  0.9 → 新建 canonical
```

为什么是 0.9：宁可少合并，不要把两个不同问题合成一条。
代价是会多建一些 canonical，人工审核时再合并。

### 4.3 同批同义

第一条新建 canonical 后，会立刻加入当前批的候选列表；
第二条即使 LLM 返回 `matched_id=null`，也会走精确标准问题匹配，
只建一条 canonical，`occurrence=2`。

### 4.4 并发与失败

- `occurrence` 用 SQL `occurrence = occurrence + 1` 原子更新；
- 幻觉 id → 当前行 skip，不改数据；
- LLM/embedding 异常 → 当前行 skip，下一轮重试；
- `--dry-run` 调模型但不写库。

代码：

- `app/core/flywheel.py`
- `app/core/prompts.py › FLYWHEEL_NORMALIZE_*`
- `scripts/flywheel.py`

---

## 5. 审核与写回

### 5.1 审核服务

`app/core/review.py`：

- `list_queue(status=None)`：默认 pending canonical，按 occurrence 降序；
- `get_detail(id)`：归并原话 + `retrieved` 快照三态；
- `approve(id, answer)`：写 FAQ，再同步 KB；
- `reject(id, reason)`：终态；
- `retry_sync(id)`：approved 的 FAQ 重试同步。

`retrieved` 三态：

| 值 | 含义 |
|---|---|
| `NULL` | 没走检索，不是知识缺口 |
| `[]` | 零命中，真缺知识 |
| `[...]` | 有召回但分数低 |

### 5.2 写回顺序

```text
approve
  → 写 faq(sync_status=pending)
  → canonical.status=approved
  → commit
  → build_kb(session=同一个 session)
       → 静态语料 + FAQ chunks
       → Chroma upsert / 删除 stale
       → faq.sync_status=synced
       → canonical.status=written_back
       → commit
  → 返回 written_back
```

同步失败：

```text
canonical 保持 approved
faq 保持 pending
API 返回 502 sync_failed
scripts/review.py retry <id>
```

### 5.3 为什么 FAQ 写表，不直接改 Markdown

- FAQ 可以整表停用/回滚；
- 不污染 `data/kb/*.md` 的版本；
- `build_kb.py` 把 FAQ 转成同构 chunk，复用 upsert/删除逻辑；
- `faq-{id}` 有独立 id，引用可追溯。

### 5.4 `kb_version`

`current_kb_version()` 把静态语料 + 已审核 FAQ 一起哈希：

```text
before: 80bd26573951
after:  865da1a0b66e
```

`/api/meta` 也用它，保证评测报告能判断“两次跑分是不是同一个库”。

### 5.5 审核 UI 与反馈入口

- 审核 UI：`/#review`，需 `ENABLE_REVIEW_API=true`；
- 队列 / 详情 / 通过 / 驳回 / 重试同步；
- C 端助手气泡下有 👍/👎：
  - `down` → `source='feedback'`、`hits=NULL`；
  - `up` → 只记日志，不落库。

---

## 6. 评测：怎么证明飞轮有用

### 6.1 数据

| 项 | 值 |
|---|---|
| badcase | 12 条当前知识库答不好的合成问题 |
| out_of_kb | 10 条明确不在知识库范围的问题 |
| fixture | `data/eval/flywheel_badcases.yaml` |
| dataset_hash | `fca8f2d1fd4b`（before/after 相同） |
| 答案 | 人工补写的合成答案，不是真实平台政策 |

### 6.2 指标

- `hit@3`：前 3 条召回 chunk 是否含期望关键词；
- `recall`：答案中出现期望关键词的比例；
- `citation_hit`：回答引用里是否出现 `faq-*`；
- `refusal_rate`：库外题走 human/fallback/transfer/明确拒答的比例。

### 6.3 before / after

| 指标 | before | after |
|---|---:|---:|
| hit@3 | 0.500 | **1.000** |
| recall | 0.667 | **0.958** |
| citation_hit | 0.000 | **1.000** |
| refusal_rate | 0.700 | **0.800** |
| kb_version | `80bd...` | `865d...` |

44 条确定性回归：**44/44**。

### 6.4 失败归因

| 现象 | 归因 |
|---|---|
| F07 recall=0.50 | 生成用「寄丢」，期望词是「丢失」；检索和引用都命中 |
| O01/O07 未拒答 | 路由到 `data`，是既有路由问题，与飞轮无关 |
| before recall=0.667 | 弱证据回答里恰好出现部分关键词，但 citation 为 0 |

---

## 7. 四个踩坑故事（STAR）

### 7.1 B07 被误判为多意图，44 条掉到 43/44

- **S**：补库后跑 44 条回归，B07 掉到 fallback 澄清；
- **T**：确认是飞轮引入还是既有行为；
- **A**：分类器把「已发货未收到 + 取消退款」标成 `multi_intent=true`；
  在意图 prompt 增加“同一售后目标的多个条件/动作不算多意图”，并补 B07 回归；
- **R**：44 条恢复 44/44。

**可讲**：「评测发现的不一定是飞轮的问题；先归因，再改产品。」

### 7.2 防止“数据库说写回成功、检索却查不到”

- **S**：approve 只写 FAQ，Chroma 可能失败；
- **T**：不能让 `written_back` 变成假状态；
- **A**：先 Chroma upsert 成功，再更新 `faq.sync_status` 和 canonical；
- **R**：失败保持 approved/pending，可 retry，测试覆盖。

**可讲**：「有外部副作用的状态机，必须最后写终态。」

### 7.3 同批同义只建一条

- **S**：同一批出现两条同义问题；
- **T**：不能建两条 canonical；
- **A**：新建 canonical 后加入当前批候选；第二条走精确匹配；
- **R**：canonical 一条，occurrence=2。

**可讲**：「批处理不是逐条孤立；同一批内的新事实要能被后续行看到。」

### 7.4 点踩不是立刻可审核

- **S**：用户点踩后以为审核队列立刻出现；
- **T**：明确反馈入口与审核队列的关系；
- **A**：点踩只写原始 `pending + normalized NULL`；Task 2 批处理后才是 canonical；
- **R**：文档和 UI 都说明“点踩先落池，批处理后进审核”。

**可讲**：「入口和审核对象不是同一层，别把‘落池’说成‘可审核’。」

---

## 8. 追问清单

| 问题 | 答题要点 |
|---|---|
| 为什么不要自动写回？ | 模型自己教自己，错误会累积；人工 approve 是安全阀 |
| 为什么复用两张表，不建 review_queue？ | 现有字段已覆盖状态/合并/次数/审核时间；新建表会重复状态 |
| 为什么批处理不做实时？ | 可重跑、不增加在线延迟；离线演示足够 |
| 为什么 LLM 之外还要 embedding？ | LLM 会误合并；0.9 阈值做二次校验，宁可少合并 |
| 为什么阈值是 0.9？ | 保守，减少不同问题被合并；代价是多建 canonical |
| occurrence 为什么要原子更新？ | 并发批处理时 read-modify-write 会丢计数 |
| 为什么 rejected 是终态？ | 审核决定不能因为同义问题再次出现而复活 |
| 为什么只有向量化成功才 written_back？ | 否则数据库说成功、检索查不到，是假闭环 |
| 为什么 FAQ 不直接改 Markdown？ | 可回滚、可停用、不污染语料版本 |
| 为什么 build 时合并，不查询时查 DB？ | 检索链路不变，Chroma 一次查询即可 |
| kb_version 怎么包含 FAQ？ | `current_kb_version = hash(静态 chunks + FAQ chunks)` |
| 12 条 badcase 能说明什么？ | 只能说明闭环和方向，不是线上统计；要主动说样本量 |
| hit@3 从 0.5 到 1.0 是不是过拟合？ | FAQ 问题与 badcase 很接近；真实流量会更难，不能直接外推 |
| 库外拒答率为什么只到 0.8？ | O01/O07 路由到 data，是既有分类问题，不是飞轮导致 |
| 同步失败怎么办？ | canonical 保持 approved、FAQ pending，API 502，retry |
| 驳回原因存哪？ | 目前只回显/日志，没有 `review_reason` 字段；这是已知边界 |
| 下一步做什么？ | 增量同步、审核权限、真实反馈、更多盲测、tool trajectory 评测 |
| 这和模型微调有什么区别？ | 飞轮补知识（FAQ/检索），微调改参数；本项目的错误主要是知识缺口 |

---

## 9. 不要说的话

| 别说 | 为什么 |
|---|---|
| 「线上数据飞轮」 | 没有真实用户流量，是离线模拟 |
| 「自动写回 / 自动学习」 | 必须人工 approve，不自动写库 |
| 「FAQ 答案是真实平台政策」 | 12 条是人工补写的合成答案 |
| 「12 条代表线上效果」 | 样本量很小，只能说明闭环和方向 |
| 「每次增量同步」 | 当前每次 approve 全量重建 chunk |
| 「审核有权限控制」 | 审核页是本地内部页，没有登录/角色 |
| 「点踩后立刻可审核」 | 点踩先写原始 pending，Task 2 批处理后才是 canonical |
| 「驳回原因已落库」 | 目前只在响应/日志里，表里没有 `review_reason` |

---

## 10. 数字速查

| 数字 | 含义 | 出处 |
|---|---:|---|
| 12 / 10 | badcase / out_of_kb | `flywheel_badcases.yaml` |
| 12 | 写入 FAQ 的 canonical 数 | `ch09-flywheel-acceptance.md` |
| 57 → 69 | 补库前 / 后 chunk 数 | before/after build |
| 0.500 → 1.000 | hit@3 | acceptance |
| 0.667 → 0.958 | answer recall | acceptance |
| 0.000 → 1.000 | FAQ citation_hit | acceptance |
| 0.700 → 0.800 | 库外拒答率 | acceptance |
| 44/44 | Ch07 确定性回归 | `regression-44.md` |
| 0.9 | embedding 二次校验阈值 | `app/core/flywheel.py` |
| 200 | 候选上限 | `app/core/flywheel.py` |
| 100 | 默认批处理 limit | `scripts/flywheel.py` |
| 5 | 低置信池状态数 | `app/db/models.py` |
| 80bd26573951 / 865da1a0b66e | before / after kb_version | acceptance |
| 1142 / 9 | 全量后端 passed / skipped | Task 8 最终验证 |
| 88 | 前端测试数 | Task 8 最终验证 |

---

## 11. 5 分钟演示脚本

### 11.1 审核 UI

```bash
# 后端（本地演示）
ENABLE_REVIEW_API=true uv run uvicorn app.main:app --port 8090
```

打开：

```text
http://127.0.0.1:5190/#review
```

展示：

1. 待审核队列，按 occurrence 降序；
2. 打开详情：原话、来源、`retrieved` 三态、归并原话；
3. 通过：填答案 → 写 FAQ → 同步 Chroma → `written_back`；
4. 驳回：终态，后续同义只累加。

### 11.2 C 端反馈

1. 打开一条助手消息；
2. 点「没帮助」；
3. 说明：写入 `low_conf_questions(source=feedback, hits=NULL)`；
4. 说明：要跑 Task 2 批处理才会变成 canonical 进审核队列。

### 11.3 评测

```bash
uv run python scripts/eval_flywheel.py --phase before
uv run python scripts/eval_flywheel.py --phase after
```

只讲三件事：

- 同一 fixture、同一 dataset_hash；
- hit@3 / citation 上升；
- 44 条回归仍是 44/44。

> 演示时主动加一句：**这是离线模拟，12 条合成数据，不是线上飞轮。**
