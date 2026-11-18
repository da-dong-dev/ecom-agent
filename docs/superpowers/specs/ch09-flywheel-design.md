# Ch09 数据飞轮设计（离线最小闭环）

> 依据：C3（飞轮：落池 → 审核 → 补库）、K1（向量同步）、K5（溯源）、K6（`kb_version`）、L1（评测）。
> 范围：**离线批处理 + 人工审核 + FAQ 写回 + 评测验证**；不做自动写回、实时入队、线上反馈闭环。
> 状态：设计稿（2026-11-16）；Task 0–8 于 2026-11-17～18 完成，
> 离线闭环与可选审核 UI 均已验收。
> 验收见 [ch09-flywheel-acceptance.md](../../reports/ch09-flywheel-acceptance.md)，
> 计划见 [ch09-flywheel.md](../plans/ch09-flywheel.md)。

---

## 0. 现状核对（先纠正事实）

本稿不假设“飞轮已经存在”。以下结论来自当前代码：

| # | 能力 | 实际现状 | 证据 |
|---|---|---|---|
| 1 | 低置信落池 | ✅ 已有：知识路弱证据会写 `low_conf_questions` | `app/graph/knowledge.py › weak_fallback`、`app/core/fallback.py › record_low_conf` |
| 2 | 低置信池字段 | ✅ 已有 `normalized / source / reason / retrieved / status / merged_into / occurrence / kb_ref / reviewed_at` | `app/db/models.py › LowConfQuestion`、`app/db/schema.sql` |
| 3 | FAQ 表 | ✅ 已有 `faq` 表，含 `source_type / source_question_id / reviewed_at / kb_version / sync_status` | `app/db/models.py › Faq`、`app/db/schema.sql` |
| 4 | FAQ 是否参与检索 | ❌ **没有**；`build_kb.py` 只读 `data/kb/`，`faq` 表当前是“建了但没用” | `scripts/build_kb.py`、`app/kb/loader.py` |
| 5 | 知识库版本 | ⚠️ `kb_version` 只哈希静态语料，且 `cached_kb_version()` 有 `lru_cache` | `app/core/kb_version.py` |
| 6 | 评测 | ✅ 已有 `/api/eval/chat`、`scripts/eval_e2e.py`、44 条评测集 | `app/api/eval.py`、`scripts/eval_e2e.py` |
| 7 | 标准化/查重 | ❌ 没有 | — |
| 8 | 审核入口（CLI/API/页面） | ❌ 没有 | — |
| 9 | FAQ 写回 + 向量化 | ❌ 没有 | — |
| 10 | 飞轮前后评测 | ❌ 没有 | — |

**结论**：Ch09 不是“加一个审核页”，而是把已有的
`low_conf_questions` 和 `faq` 两张表接成一条**可验证的数据管线**；
否则池子只会越积越多，FAQ 表永远空着。

---

## 1. 目标与边界

### 1.1 要解决的问题

1. **池子只进不出**：低置信问题已经落池，但没有标准化、去重、审核、写回，问题不会变成知识。
2. **FAQ 表没有闭环**：`faq` 有字段但没有写入路径，也没有被检索使用。
3. **无法证明有效**：补库前后没有同批题对比，无法回答“飞轮到底有没有用”。
4. **无真实流量**：当前是合成数据/演示场景，不能做“线上自动飞轮”，只能做**离线模拟飞轮**。

### 1.2 目标

1. 把 `low_conf_questions` 中的原始问题标准化为 FAQ 式问题，并合并同义问题。
2. 提供人工审核入口（先 CLI，后可选 UI），支持通过/驳回。
3. 审核通过后写入 `faq`，把 FAQ 合并进 Chroma，使下一问能检索命中。
4. 用同一批 badcase 做补库前后对比，并检查库外题拒答率与 44 条回归。
5. 文档明确标注“离线模拟飞轮，无真实用户流量”，不夸大。

### 1.3 非目标

| 不做 | 原因 |
|---|---|
| 自动生成答案并写库 | 没有人工确认的飞轮 = 模型自己教自己，错误会累积 |
| 实时入队/实时写回 | 每条兜底触发一次模型，成本和复杂度都高；批处理足够 |
| 线上用户反馈闭环 | 当前没有真实流量、没有登录、没有反馈入口 |
| 模型微调/重训 | 飞轮补的是知识，不是模型参数 |
| 多级审批/角色权限/多租户 | 单人本地项目，没有意义 |
| B 端坐席工作台 | 后续配套，不属于 Ch09 |

### 1.4 验收标准

1. 用户问一个知识库外的问题 → 走兜底 → `low_conf_questions` 多一行，`retrieved` 三态正确。
2. 跑批处理后，原始问题变成 canonical 行：有 `normalized`，同义问题只累加 `occurrence`。
3. 审核驳回后，该 canonical 为终态；后续同义问题只累加，不复活。
4. 审核通过后，`faq` 多一行；`build_kb.py` 把 `faq-{id}` 写进 Chroma；同一问题再问能命中并引用。
5. 补库前后评测：badcase 的 `hit@3` 上升；库外题拒答率不下降；44 条确定性回归仍为 44/44。
6. `kb_version` 包含 FAQ；`/api/meta` 能反映写回后的版本。
7. 上述流程有自动化测试与 `docs/reports/ch09-flywheel-acceptance.md`。

---

## 2. 关键决策

| # | 决策 | 选择 | 理由 | 备选/代价 |
|---|---|---|---|---|
| D-F1 | 队列表 | **复用 `low_conf_questions` + `faq`，不新建 `review_queue`** | 现有字段已覆盖 `normalized/status/merged_into/occurrence/kb_ref/reviewed_at`；新建表会重复状态并增加迁移 | MewHelp 用独立 `review_queue` 更清晰，但我们的池子表本来就是按飞轮设计的 |
| D-F2 | 处理方式 | **批处理，不做实时** | 可重跑、不增加在线延迟、逻辑简单 | 实时入队成本高，暂不做 |
| D-F3 | 人工审核 | **必须人工通过/驳回，禁止自动写回** | 防止模型错误进入知识库 | 自动写回不可接受 |
| D-F4 | 写回位置 | **写 `faq` 表，再合并进 Chroma** | FAQ 独立于源文档，可回滚、可整表停用 | 直接改 `data/kb/*.md` 会污染文档版本 |
| D-F5 | 状态机 | 见 §3.3 | 状态必须可审计、可重试 | 无状态会导致重复处理 |
| D-F6 | 查重 | **LLM 标准化 + embedding 二次校验** | LLM 会漏判；embedding 兜底 | 多一次 embedding 调用；MewHelp 实际只做 LLM 候选比对 |
| D-F7 | 审核入口 | **先 CLI，UI 可选** | 先验证闭环，避免把 Ch09 做成前端练习 | UI 约 +1–1.5 天 |
| D-F8 | 数据来源 | **离线模拟：10–20 条合成 badcase + 10 条库外题** | 没有真实流量，不能假装线上飞轮 | README/报告必须标注“离线模拟” |
| D-F9 | 审核 API | 加 `ENABLE_REVIEW_API` 开关，默认关闭 | 审核会写知识库，不应默认暴露 | 本地演示时在 `.env` 打开 |
| D-F10 | 评估结果 | MVP 写 JSON/Markdown 报告，不新增 `eval_runs` 表 | 当前只有单次/少量运行，文件足够 | 需要趋势时再加表 |
| D-F11 | FAQ 检索合并 | **构建时合并**（`build_kb.py` 读 `faq` 表），不在查询时查 DB | 检索链路不变，Chroma 一次查询即可 | FAQ 写回后需要重建/增量同步 |

---

## 3. 数据模型

### 3.1 复用 `low_conf_questions`

现有字段：

| 字段 | 飞轮用途 |
|---|---|
| `id` | 原始问题 / canonical 行 id |
| `conversation_id` | 回放来源 |
| `question` | 用户原话 |
| `normalized` | 标准化后的 FAQ 式问题；`NULL` = 尚未处理 |
| `source` | `retrieval_low_conf` / `feedback` / `rule_conflict` |
| `reason` | 低置信原因、规则冲突原因 |
| `retrieved` | 三态 JSON：`NULL` / `[]` / `[...]` |
| `status` | 见 §3.3 |
| `merged_into` | 同义问题指向 canonical 行 |
| `occurrence` | 出现次数，决定审核优先级 |
| `kb_ref` | 写回后的 FAQ 引用，格式 `faq-{faq.id}` |
| `reviewed_at` | 审核时间 |

**新增字段**：

```sql
ALTER TABLE low_conf_questions ADD COLUMN suggested_answer TEXT;
-- 仅当列不存在时执行；迁移脚本必须幂等
```

**新增索引**：

```sql
CREATE INDEX IF NOT EXISTS idx_low_conf_status ON low_conf_questions(status);
CREATE INDEX IF NOT EXISTS idx_low_conf_merged_into ON low_conf_questions(merged_into);
```

### 3.2 `faq` 表

现有字段已经够用：

| 字段 | 用途 |
|---|---|
| `question` | canonical 的标准问题 |
| `answer` | 人工核准答案 |
| `source_type` | `flywheel` / `manual` |
| `source_question_id` | 来自哪条 canonical `low_conf_questions.id` |
| `reviewed_at` | 审核时间 |
| `kb_version` | 写回时的知识库版本 |
| `sync_status` | `pending` / `synced` |

**新增唯一部分索引**（防重复写回）：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uk_faq_source_question
ON faq(source_question_id)
WHERE source_type = 'flywheel' AND source_question_id IS NOT NULL;
```

“部分唯一索引”= 只对 `WHERE` 命中的行做唯一约束；手工 FAQ 不受影响。

### 3.3 状态机

| 状态 | 条件 | 含义 |
|---|---|---|
| `pending` | `normalized IS NULL` | 原始问题，等待批处理 |
| `pending` | `normalized IS NOT NULL AND merged_into IS NULL` | canonical，等待人工审核 |
| `merged` | `merged_into = canonical_id` | 同义原话，已归并，不单独审核 |
| `approved` | canonical 审核通过，等待 KB 同步 | 可重试同步 |
| `written_back` | FAQ 已写、向量化成功 | 完成 |
| `rejected` | canonical 被驳回 | 终审，不复活 |

**状态流转**：

```
pending(normalized NULL)
  ├─ 新建 ──► pending(canonical, normalized 有值)
  └─ 命中 ──► merged(merged_into=canonical)
pending(canonical)
  ├─ 通过 ──► approved ──同步成功──► written_back
  └─ 驳回 ──► rejected
approved
  └─ 同步失败 ──► approved（faq.sync_status=pending，可重试）
```

**关键规则**：

- `rejected` 是终态；候选查重包含已驳回行，但只累加 `occurrence`，不改状态。
- `written_back` 也是终态；同一 canonical 不会再次写 FAQ（唯一索引兜底）。
- 审核列表只显示 `pending AND normalized IS NOT NULL AND merged_into IS NULL`。

### 3.4 `retrieved` 三态

沿用现有语义：

| 值 | 含义 | 审核时怎么用 |
|---|---|---|
| `NULL` | 没走检索（规则类等） | 不是知识缺口，一般不补 FAQ |
| `[]` | 走了检索但零命中 | 真缺知识，优先补 |
| `[...]` | 有召回但分数低 | 可能只是阈值/检索问题 |

审核详情页用 `load_chunks()` 把 `retrieved` 里的 id 映射回 `section` / `text`，展示给人看。

---

## 4. 流程设计

### 4.1 全景

```
① 入口
   知识路弱证据（已有） / 用户点踩（可选） / 规则冲突（可选）
        ↓
② 落池 low_conf_questions
   question + source + reason + retrieved 三态
        ↓
③ 批处理 scripts/flywheel.py
   LLM 标准化 + embedding 查重
        ↓
④ 人工审核 scripts/review.py 或 /api/review/*
   通过 → faq(pending)
   驳回 → rejected（终审）
        ↓
⑤ 写回
   faq 行 → faq-{id} chunk → embedding → Chroma upsert
   → faq.sync_status=synced → low_conf.status=written_back
        ↓
⑥ 评测
   补库前/后同一批 badcase + 库外题拒答率 + 44 条回归
```

### 4.2 入口

#### 低置信入口（已有，不改）

`app/graph/knowledge.py › weak_fallback` 已经调用 `record_low_conf`：

- `source=retrieval_low_conf`
- `reason` 区分机械弱 / 语义弱
- `retrieved` 三态

Ch09 只需要消费这些行，不需要改落池逻辑。

#### 用户点踩（可选，第二阶段）

新增 `POST /api/feedback`：

```json
{
  "conversation_id": 1,
  "message_id": 12,
  "rating": "down",
  "question": "猫窝能水洗吗"
}
```

- `down` → `low_conf_questions(source='feedback')`
- `up` → 只记日志，不落库
- 归属校验复用 Ch06
- 前端在助手气泡下加 👍/👎，一次性点击

**限制**：如果历史接口没有保存完整检索快照，`feedback` 行的 `retrieved` 可以为 `NULL`；
这不会阻塞落池，但审核时少了证据。

#### 规则冲突（可选，第二阶段）

`rules_exit` 在模型与规则分歧、转人工时：

```python
record_low_conf(
    question=query,
    source="rule_conflict",
    reason=f"rule_conflict:{rule_id}",
    hits=None,
)
```

这类样本更多用于发现规则/字段问题，不一定补 FAQ，但值得进池。

### 4.3 批处理：标准化 + 查重

新增 `app/core/flywheel.py` 和 `scripts/flywheel.py`。

**游标**：

```sql
WHERE status = 'pending' AND normalized IS NULL
ORDER BY id
LIMIT 100
```

处理完写 `normalized`，重复跑不会重复处理。

**候选**：

```sql
WHERE normalized IS NOT NULL AND merged_into IS NULL
ORDER BY occurrence DESC, id DESC
LIMIT 200
```

包含 `pending / approved / written_back / rejected`，以便：
- 同义问题归并；
- 已驳回的问题只累加，不复活。

**LLM 输出**（结构化）：

```json
{
  "normalized_question": "猫窝是否支持水洗",
  "matched_id": 12,
  "suggested_answer": "可以，以平台售后规则为准"
}
```

Prompt 约束：

- 剥掉情绪、口语、无关细节；
- 问法不同但同一件事算命中；
- 只能填候选里出现过的 id，**宁可 null 也不硬凑**；
- 示例答案不臆造政策数字。

**embedding 二次校验**：

- 计算 `normalized_question` 与候选标准问题的相似度；
- LLM 命中但相似度 < 0.9 → 当新问题；
- LLM 未命中但最高相似度 ≥ 0.9 → 归并到最相似候选；
- 仍不确定 → 新建，交给人审。

**写回池子**：

| 情况 | 操作 |
|---|---|
| 命中 | `canonical.occurrence += 1`（原子 SQL）；原话 `merged_into=canonical.id`，`status='merged'` |
| 未命中 | 原话写 `normalized`、`suggested_answer`，保持 `pending`，成为 canonical |
| LLM 失败/坏 JSON/幻觉 id | 跳过，`normalized` 留空，下轮重试 |
| 同批同义 | 串行处理，第二条能看到第一条刚建的行 |

### 4.4 人工审核

**CLI（MVP）**：

```bash
uv run python scripts/review.py list
uv run python scripts/review.py show 12
uv run python scripts/review.py approve 12 --answer "可以水洗，注意阴干"
uv run python scripts/review.py reject 12 --reason "活动已过期"
```

`list` 按 `occurrence DESC, id DESC` 排序；`show` 展示：
- 归并原话与 `source` / `reason`；
- `retrieved` 快照（id 映射回 section/text）；
- `suggested_answer`。

**HTTP API（给后续 UI）**：

| 接口 | 作用 |
|---|---|
| `GET /api/review/queue?status=pending` | 待审列表 |
| `GET /api/review/{id}` | 详情 + 归并原话 + 快照 |
| `POST /api/review/{id}/approve` | `{answer}`，写 FAQ 并同步 KB |
| `POST /api/review/{id}/reject` | `{reason?}`，终审 |

- 只有 `pending + normalized + merged_into IS NULL` 能流转；
- 404：不存在；409：状态不可流转；422：答案为空；502：KB 同步失败，可重试；
- `ENABLE_REVIEW_API=false` 时返回 404（与 `ENABLE_EVAL_API` 一致）。

### 4.5 写回 FAQ 与检索合并

**通过时**：

1. 写 `faq`：
   - `question = canonical.normalized`
   - `answer = approved_answer`
   - `source_type = 'flywheel'`
   - `source_question_id = canonical.id`
   - `reviewed_at = now`
   - `sync_status = 'pending'`
2. canonical 状态 → `approved`。
3. 触发 KB 同步（`build_kb.py` 或内部函数）。

**`build_kb.py` 的改动**：

- 除 `load_chunks()` 外，读取 `faq` 中 `reviewed_at IS NOT NULL` 且
  `sync_status IN ('pending','synced')` 的行；
- 生成 chunk：

```python
{
    "id": f"faq-{row.id}",
    "text": f"{row.question}\n{row.answer}",
    "source": "faq",
    "section": row.question,
    "kind": "faq",
}
```

- 计算 `kb_version` 时**包含 FAQ**；
- upsert 到同一个 Chroma collection；
- 删除已不在语料中的旧 id（包括被停用的 FAQ）；
- 成功后把该 FAQ 行更新为 `sync_status='synced'`、`kb_version=version`；
- 把 canonical 更新为 `written_back`、`kb_ref=f"faq-{row.id}"`。

**检索代码不用改**：FAQ 和静态文档在同一个 collection，`search()` 自然能召回；
引用里会出现 `faq-{id}`。

### 4.6 失败与补偿

SQLite 与 Chroma 没有分布式事务，必须明确失败行为：

| 失败点 | 行为 |
|---|---|
| 写 FAQ 行失败 | 事务回滚，canonical 保持 `pending` |
| 写 FAQ 成功，embedding/upsert 失败 | `faq.sync_status` 保持 `pending`，canonical 保持 `approved`；返回 502，可重试 |
| 重试同步 | 重新跑 `build_kb`；`faq-{id}` 固定，upsert 幂等 |
| 唯一索引冲突 | 视为并发/重复审批，读取已有 FAQ 并继续同步 |
| 状态更新失败 | 记录日志；以 `faq.sync_status` 和 `kb_version` 为准，人工核对 |

**原则：只有向量化成功，才允许 `written_back`。** 不允许“审核页显示通过，但检索不到”。

### 4.7 `kb_version` 与 `/api/meta`

当前 `kb_version` 只哈希 `data/kb/`，`cached_kb_version()` 带 `lru_cache`。
FAQ 写回后必须让版本变化，否则评测报告不可比。

方案：

1. `build_kb.py` 用“静态 chunks + FAQ chunks”计算版本；
2. `/api/meta` 改为包含 FAQ 的版本；
3. 去掉 `lru_cache`（本地语料小，开销可忽略），或加显式 reload；
4. 评测报告同时记录 `kb_version` 和 `dataset_hash`。

### 4.8 评测

新增 `scripts/eval_flywheel.py`：

**数据集**：`data/eval/flywheel_badcases.yaml`

```yaml
badcases:
  - id: F01
    question: "猫窝能水洗吗"
    expected_keywords: ["水洗", "阴干"]
out_of_kb:
  - id: O01
    question: "你们支持以旧换新吗"
```

**流程**：

```bash
# 补库前
uv run python scripts/eval_flywheel.py --phase before

# 审批 + build_kb

# 补库后
uv run python scripts/eval_flywheel.py --phase after
```

**指标**：

| 指标 | 看什么 |
|---|---|
| `hit@3` | 补的 FAQ 是否进前 3 |
| `recall` | 正确证据是否被召回 |
| `citation_hit` | 回答是否引用 `faq-{id}` |
| `out_of_kb_refusal` | 库外题拒答率是否保持不变 |
| `44/44` | Ch07 确定性回归是否退化 |

**验收线**：

- badcase `hit@3` 上升；
- 库外题拒答率不下降；
- 44 条回归仍为 44/44；
- 报告写清 `kb_version`、数据集哈希、样本量。

**可复现性**：`before` 必须在“尚无 flywheel FAQ”的库/集合上跑；重跑前恢复
`data/app.db` 与 Chroma 集合快照，或保留第一次 before 结果。不要在已补库的库上重跑 before。

报告：`docs/reports/ch09-flywheel-acceptance.md`。

---

## 5. 模块与接口

### 5.1 新增/修改文件

| 文件 | 作用 |
|---|---|
| `app/db/models.py` | `LowConfQuestion.suggested_answer`；FAQ 唯一索引 |
| `app/db/schema.sql` | 同上，供新库使用 |
| `scripts/migrate_ch09.py` | 老库 `ALTER TABLE` + 索引，幂等 |
| `app/core/flywheel.py` | 标准化、查重、状态流转、approve/reject 服务 |
| `app/core/prompts.py` | 飞轮标准化 prompt |
| `app/api/review.py` | 审核 API（受 `ENABLE_REVIEW_API` 控制） |
| `scripts/flywheel.py` | 批处理 CLI |
| `scripts/review.py` | 审核 CLI |
| `app/kb/loader.py` | `faq_chunks()`：FAQ 行转 chunk |
| `scripts/build_kb.py` | 合并 FAQ、更新 `sync_status/kb_version` |
| `app/core/kb_version.py` | 版本包含 FAQ |
| `app/main.py` | `/api/meta` 版本、注册 review router |
| `app/config.py` | `enable_review_api` |
| `scripts/eval_flywheel.py` | 补库前后评测 |
| `data/eval/flywheel_badcases.yaml` | 合成 badcase + 库外题 |
| `tests/...` | 迁移、批处理、审核、写回、评测 |

### 5.2 不新增的表

- 不新建 `review_queue`；
- 不新建 `eval_runs`（MVP 用报告文件）；
- 不新建向量 collection（FAQ 与静态文档共用）。

---

## 6. 测试矩阵

| 层 | 测什么 |
|---|---|
| DB | 新列/索引；唯一部分索引阻止重复 FAQ；迁移重复执行幂等 |
| 批处理 | 新建 canonical；同义归并；`occurrence` 累加；驳回不复活；幻觉 id 跳过；坏 JSON 跳过；同批串行去重 |
| 审核 | 列表/详情；approve 写 FAQ；reject 终态；非 pending 409；空答案 422；重复 approve 不重复写 |
| 写回 | FAQ chunk 生成；`kb_version` 变化；Chroma upsert/删除；`sync_status`；失败可重试 |
| 检索 | 同一问题补库后命中 `faq-*`；库外题仍不命中 |
| 评测 | before/after 指标；库外拒答率；44/44 回归 |
| 安全 | 审核 API 默认关闭；不返回内部字段；无真实 PII |

**验证命令**：

```bash
uv run pytest -q
uv run ruff check .
uv run python scripts/init_db.py
uv run python scripts/migrate_ch09.py --db data/app.db
uv run python scripts/migrate_ch09.py --db data/eval.db
uv run python scripts/flywheel.py --limit 100
uv run python scripts/review.py list
```

---

## 7. 工作量与任务映射

| 阶段 | 内容 | 估算 |
|---|---|---|
| Task 0 | 基线 + 合成数据 fixture | 0.5 天 |
| Task 1 | 迁移 + 索引 | 0.5 天 |
| Task 2 | 标准化/查重批处理 | 1–1.5 天 |
| Task 3 | 审核服务 + CLI | 1 天 |
| Task 4 | FAQ 写回 + KB 合并 + 检索 | 1.5 天 |
| Task 5 | 点踩/规则冲突入口（可选） | 0.5–1 天 |
| Task 6 | 前后评测 + 验收报告 | 1 天 |
| Task 7 | 审核 UI（可选） | 1–1.5 天 |

**CLI 版合计：5–6 人日；加 UI：6.5–7.5 人日。**

详细步骤见 [ch09-flywheel.md](../plans/ch09-flywheel.md)。

---

## 8. 本章不做

| 不做 | 原因 |
|---|---|
| 自动写回 | 无人工确认，错误会累积 |
| 实时入队 | 成本高，批处理够用 |
| 线上用户反馈闭环 | 没有真实流量与登录体系 |
| 模型微调 | 补知识不等于改模型 |
| 多级审批/角色/多租户 | 单人本地项目无必要 |
| 自动删除/合并 FAQ | 先人工审核，后续再谈 |
| 用真实用户数据做演示 | 隐私与合规风险 |

---

## 9. 开放问题（默认值）

| # | 问题 | 默认 |
|---|---|---|
| 1 | 是否做用户点踩入口？ | 第二阶段做；第一版只消费已有低置信池 |
| 2 | 是否做规则冲突入口？ | 第二阶段做；先不动 `rules_exit` |
| 3 | 审核页面用 CLI 还是 UI？ | 先 CLI；UI 作为可选 Task 7 |
| 4 | 是否新增 `eval_runs` 表？ | 不新增；先写 JSON/MD 报告 |
| 5 | FAQ 是否需要删除/下架？ | MVP 不做；`build_kb` 的 stale 删除只处理手工移除的行 |
| 6 | 审核 API 是否鉴权？ | 本地演示不鉴权，但默认关闭（`ENABLE_REVIEW_API=false`）；接真实数据前必须加 |

---

## 10. 与 MewHelp 的差异（只借范式，不抄代码）

| 维度 | MewHelp | 本设计 |
|---|---|---|
| 队列表 | 独立 `review_queue` | 复用 `low_conf_questions` |
| 查重 | LLM 候选比对 | LLM + embedding 二次校验 |
| 审核入口 | HTML `/review` | 先 CLI，后可选 antd 页面 |
| 写回 | `knowledge_chunks` + Milvus | `faq` 表 + Chroma |
| 评估 | `eval_runs` 趋势表 | MVP 用报告文件 |
| 认证 | demo 后台无鉴权 | 默认关闭审核 API，接真实数据前加鉴权 |

**借鉴的只是流程与护栏**：批处理、游标幂等、`occurrence` 累加、驳回终审、
三态快照、写回后向量化、失败补偿。**不复制任何源码。**

---

## 11. 面试要点

- 为什么批处理而不是实时：不增加在线延迟，可重跑，逻辑简单。
- 为什么必须人审：没有人工确认的飞轮等于模型自己教自己。
- 为什么复用两张表：已有字段就是状态机，避免重复建模。
- 为什么要 embedding 二次校验：LLM 会漏判同义问题。
- 为什么写回后要重建：审核通过但检索不到 = 假闭环。
- 为什么 `kb_version` 要包含 FAQ：否则补库前后指标不可比。
- 为什么说“离线模拟”：没有真实流量，不能把演示包装成线上飞轮。
