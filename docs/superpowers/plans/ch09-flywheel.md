# Ch09 数据飞轮 实施计划

> **For agentic workers:** 按 task 顺序执行，步骤用 `- [ ]` 追踪。
> 每个 task 完成、用户确认后再 commit；不要自动 push。
> 实现与 spec 有偏差时，**同一个 task 内同步更新 spec 和本 plan**。

**Goal:** 把已有的低置信池接成一条可验证的飞轮：
落池 → 标准化查重 → 人工审核 → FAQ 写回 → 检索命中 → 补库前后评测。

**Architecture:** 复用 `low_conf_questions` 作为“原话池 + 待审队列”，
复用 `faq` 作为写回目标；批处理离线跑，人工审核，写回后由
`build_kb.py` 把 FAQ 合并进 Chroma。不做实时入队、不自动写回。

**Tech Stack:** FastAPI / async SQLAlchemy + aiosqlite / SQLite / Chroma / Ollama；
评测沿用 Ch07 的 `/api/eval/chat` 与 `scripts/eval_e2e.py`；不引入新运行时依赖。

**状态：** Task 0–2 完成（2026-11-17～18）、Task 3 完成（2026-11-18）；
Task 4 未开始。改动记录见 [ch09-flywheel-changes.md](../../reports/ch09-flywheel-changes.md)。

**Spec:** [ch09-flywheel-design.md](../specs/ch09-flywheel-design.md)
**前置:** Ch01–Ch07 已验收；建议 Ch08 Task 12 完成后再开工。
若只先做后端，可在 Ch08 Task 10 后并行；Ch09 不依赖 Ch08 的断流重连。
**数据前提:** 没有真实用户流量；本计划用 10–20 条合成 badcase + 10 条库外题，
所有文档必须标注“离线模拟飞轮”。

---

## 术语表（首次出现先看这里）

| 术语 | 白话解释 |
|---|---|
| **飞轮** | 把系统答不好的问题收集起来，人工审核后补进知识库，再用评测验证效果，形成循环 |
| **落池** | 把答不好的问题写进 `low_conf_questions`，等后续处理 |
| **归一化 / 标准化** | 把口语原话改写成 FAQ 式标准问题，例如“猫窝脏了怎么洗” → “猫窝是否支持水洗” |
| **查重** | 判断新问题与已有标准问题是不是同一件事；是就合并，不重复建 |
| **canonical（标准行）** | 一个知识缺口的主记录；同义原话都归并到它 |
| **写回** | 审核通过后，把标准问题和核准答案写进 `faq`，并进入检索 |
| **向量化** | 把文本变成 embedding 向量，写进 Chroma，供检索使用 |
| **幂等（idempotent）** | 同一操作重复执行多次，效果和执行一次一样 |
| **部分唯一索引** | 只对满足 `WHERE` 条件的行做唯一约束；这里用于“同一个待审项只能写回一次 FAQ” |
| **kb_version** | 知识库内容哈希；补库前后版本不同，评测数字才可比 |
| **补偿** | SQLite 与 Chroma 没有分布式事务，失败后靠删除/重试把状态修正 |
| **离线模拟飞轮** | 没有真实用户流量，用合成 badcase 模拟“发现缺口 → 补库 → 验证”的闭环 |

---

## Global Constraints

- **不自动 commit / push**：每个 task 完成后等用户确认。
- **spec / plan 同步**：实现偏离设计时，同一个 task 内更新两份文档。
- **改动记录**：每个 task 追加 `docs/reports/ch09-flywheel-changes.md`，
  并在 `00-change-log-overview.md` 登记。
- **禁止自动写回**：所有 FAQ 写回必须经过人工 `approve`；`reject` 是终审。
- **只有 `pending` 可流转**：`approved / written_back / rejected` 不能再审批。
- **只有向量化成功才算 `written_back`**：不允许“审核通过但检索不到”。
- **审核 API 默认关闭**：`ENABLE_REVIEW_API=false`；本地演示再打开。
- **不新增 `review_queue` / `eval_runs` 表**：MVP 复用现有表 + 报告文件。
- **不复制 MewHelp 源码**：只借流程与护栏；代码按本项目风格重写。
- **每个 task 的验证必须可复现**：命令写进 task，不能只写“手工看过”。

---

## Task 0: 基线核对 + 合成 badcase fixture

**目的：** 先确认当前基线，再准备可重复的离线数据；没有数据就无法验收飞轮。

**Files:**
- 新建：`data/eval/flywheel_badcases.yaml`
- 新建/追加：`docs/reports/ch09-flywheel-changes.md`（先写 Task 0 一节）
- 不改产品代码。

**Step 1: 记录基线**

```bash
cd /Users/lidong/Desktop/workFile/ecom-agent
uv run pytest -q
uv run ruff check .
uv run python scripts/init_db.py
```

记录当前 passed / skipped 数字、`kb_version`、Chroma 集合名。
如果基线不一致，先停下来查，不要在错误基线上开发。

**Step 2: 准备 badcase**

`data/eval/flywheel_badcases.yaml`：

```yaml
badcases:
  - id: F01
    question: "猫窝能水洗吗"
    expected_keywords: ["水洗", "阴干"]
  - id: F02
    question: "猫爬架保修几年"
    expected_keywords: ["保修"]
  # 10–20 条；问题必须是当前知识库确实答不好的

out_of_kb:
  - id: O01
    question: "你们支持以旧换新吗"
  # 10 条；必须明确不在当前知识库中
```

要求：

- 不包含真实手机号、地址、姓名；
- `badcases` 至少 10 条，`out_of_kb` 至少 10 条；
- 每条 badcase 有期望关键词，供确定性评测使用。

**Step 3: 录入池子**

写一个一次性脚本或直接在测试 fixture 中插入：

```python
await record_low_conf(
    question="猫窝能水洗吗",
    reason="offline_flywheel_fixture",
    hits=[],
)
```

不要改生产代码；可以用 `tests/` fixture 或 `scripts/flywheel.py --seed-fixtures`（Task 2 再加）。

**验证：**

```bash
uv run python - <<'PY'
import yaml
d = yaml.safe_load(open("data/eval/flywheel_badcases.yaml"))
assert len(d["badcases"]) >= 10
assert len(d["out_of_kb"]) >= 10
print("badcases", len(d["badcases"]), "out_of_kb", len(d["out_of_kb"]))
PY
```

**Commit:** `docs(ch09): 飞轮合成 badcase 与基线记录`

---

## Task 1: 数据层：`suggested_answer` + 索引 + 迁移

**目的：** 让池子能存“模型示例答案”，并防重复写回。

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/schema.sql`
- Create: `scripts/migrate_ch09.py`
- Modify: `scripts/init_db.py`（如需要，只补索引检查）
- Create/Modify: `tests/db/test_migrate_ch09.py`、`tests/db/test_models.py`

**Step 1: ORM**

`LowConfQuestion` 增加：

```python
suggested_answer: Mapped[str | None] = mapped_column(Text)
```

`__table_args__` 增加：

```python
Index("idx_low_conf_status", "status"),
Index("idx_low_conf_merged_into", "merged_into"),
```

`Faq` 增加唯一部分索引：

```python
Index(
    "uk_faq_source_question",
    "source_question_id",
    unique=True,
    sqlite_where=text(
        "source_type = 'flywheel' AND source_question_id IS NOT NULL"
    ),
)
```

**Step 2: schema.sql**

```sql
-- low_conf_questions
suggested_answer TEXT,
...
CREATE INDEX IF NOT EXISTS idx_low_conf_status ON low_conf_questions(status);
CREATE INDEX IF NOT EXISTS idx_low_conf_merged_into ON low_conf_questions(merged_into);

-- faq
CREATE UNIQUE INDEX IF NOT EXISTS uk_faq_source_question
ON faq(source_question_id)
WHERE source_type = 'flywheel' AND source_question_id IS NOT NULL;
```

**Step 3: 迁移脚本**

`scripts/migrate_ch09.py`：

```bash
uv run python scripts/migrate_ch09.py --db data/app.db
uv run python scripts/migrate_ch09.py --db data/eval.db
```

要求：

- 标准库 `sqlite3`；
- `PRAGMA table_info(low_conf_questions)` 缺列才 `ALTER TABLE ... ADD COLUMN suggested_answer TEXT`；
- `CREATE INDEX IF NOT EXISTS ...`；
- 重复执行幂等；
- 打印“已迁移 / 已是最新”。

**Step 4: 测试**

- 新列写入/读回；
- 同一 `source_question_id` 写第二条 flywheel FAQ 抛 IntegrityError；
- `manual` FAQ 不受唯一索引影响；
- 迁移脚本跑两次都成功；
- `uv run python scripts/init_db.py` 仍打印 schema 一致。

**验证：**

```bash
uv run pytest tests/db -q
uv run python scripts/init_db.py
uv run python scripts/migrate_ch09.py --db data/app.db
uv run python scripts/migrate_ch09.py --db data/app.db
```

**Commit:** `feat(ch09): 飞轮字段与唯一索引迁移`

---

## Task 2: 批处理：标准化 + 查重

**目的：** 把原始池子变成可审核的 canonical 行。

**Files:**
- Create: `app/core/flywheel.py`
- Modify: `app/core/prompts.py`
- Create: `scripts/flywheel.py`
- Create: `tests/core/test_flywheel.py`

**Step 1: Prompt**

在 `app/core/prompts.py` 增加 `FLYWHEEL_NORMALIZE_SYSTEM` / `FLYWHEEL_NORMALIZE_PROMPT`：

- 输入：用户原话 + 候选 `(id, normalized_question)`；
- 输出：`normalized_question`、`matched_id`、`suggested_answer`；
- 约束：去情绪、同义合并、只填候选 id、宁可 null、不臆造政策数字。

**Step 2: 核心类型与函数**

`app/core/flywheel.py`：

```python
class NormalizeResult(BaseModel):
    normalized_question: str
    matched_id: int | None = None
    suggested_answer: str = ""

async def normalize_and_match(raw_question: str, candidates: list[dict]) -> NormalizeResult:
    ...

async def process_pending(limit: int = 100) -> dict:
    """返回 {processed, merged, created, skipped}。"""
```

`process_pending` 逻辑：

1. 取 `status='pending' AND normalized IS NULL`，按 id 升序；
2. 取候选：`normalized IS NOT NULL AND merged_into IS NULL`，按
   `occurrence DESC, id DESC` 限 200；
3. 逐条串行调用 `normalize_and_match`；
4. embedding 二次校验（阈值 0.9）；
5. 命中 → 原子 `occurrence += 1`，原话 `merged_into=canonical.id`、`status='merged'`；
6. 未命中 → 原话写 `normalized/suggested_answer`，保持 `pending`；
7. 异常/幻觉 id → `skipped`，不改行，下轮重试。

**Step 3: CLI**

`scripts/flywheel.py`：

```bash
uv run python scripts/flywheel.py --limit 100
uv run python scripts/flywheel.py --dry-run
```

输出本轮统计：`processed / merged / created / skipped`。

可选：`--seed-fixtures` 把 `flywheel_badcases.yaml` 的 badcase 插入池子，
仅用于本地演示，默认不执行。

**Step 4: 测试（stub LLM，不打真实模型）**

- 新问题 → 新建 canonical；
- 命中 → `occurrence` 加 1，原话 `status='merged'`；
- 已驳回候选 → 只累加，不复活；
- 幻觉 id → skip，游标不推进；
- 坏 JSON / LLM 异常 → skip；
- 同批两条同义 → 只建一行，`occurrence=2`；
- 重复跑 → 不重复处理。

**验证：**

```bash
uv run pytest tests/core/test_flywheel.py -q
uv run python scripts/flywheel.py --dry-run
```

**Commit:** `feat(ch09): 飞轮标准化与查重批处理`

---

## Task 3: 审核服务 + CLI

**目的：** 让人能看队列、看证据、通过/驳回。

**Files:**
- Modify: `app/core/flywheel.py`（或新建 `app/core/review.py`）
- Create: `app/api/review.py`
- Modify: `app/main.py`（注册 router）
- Modify: `app/config.py`（`enable_review_api`）
- Create: `scripts/review.py`
- Create: `tests/api/test_review_api.py`、`tests/core/test_review_service.py`

**Step 1: 服务函数**

```python
async def list_queue(status: str | None = None) -> list[dict]: ...
async def get_detail(review_id: int) -> dict | None: ...
async def approve(review_id: int, answer: str) -> dict: ...
async def reject(review_id: int, reason: str | None = None) -> dict: ...
```

规则：

- 只有 `status='pending' AND normalized IS NOT NULL AND merged_into IS NULL` 可流转；
- `approve` 写 `faq(pending)`，canonical → `approved`；
- `reject` → `rejected`，写 `reviewed_at`；
- 空答案 422；不存在 404；状态不可流转 409。

**Step 2: API**

`app/api/review.py`：

| 方法 | 路径 |
|---|---|
| GET | `/api/review/queue` |
| GET | `/api/review/{id}` |
| POST | `/api/review/{id}/approve` |
| POST | `/api/review/{id}/reject` |

`app/main.py` 注册 router；`ENABLE_REVIEW_API=false` 时所有接口 404。

**Step 3: 详情**

`get_detail` 返回：

- canonical 行；
- 归并原话（`merged_into = id`），带 `source / reason / created_at`；
- `retrieved` 解析后的快照（id → `load_chunks()` 的 section/text）；
- `suggested_answer`。

**Step 4: CLI**

```bash
uv run python scripts/review.py list
uv run python scripts/review.py show 12
uv run python scripts/review.py approve 12 --answer "可以水洗，注意阴干"
uv run python scripts/review.py reject 12 --reason "活动已过期"
```

**Step 5: 测试**

- list 按 occurrence 降序；
- detail 带原话与快照；
- approve 写 FAQ + 状态 `approved`；
- reject 终态，后续 approve 409；
- 重复 approve 不重复写 FAQ（Task 4 接同步后补）；
- API 开关关闭时 404。

**验证：**

```bash
uv run pytest tests/api/test_review_api.py tests/core/test_review_service.py -q
uv run python scripts/review.py list
```

**Commit:** `feat(ch09): 飞轮审核服务与 CLI`

---

## Task 4: FAQ 写回 + KB 合并 + 检索验证

**目的：** 审核通过后，下一问真的能检索到。

**Files:**
- Modify: `app/kb/loader.py`
- Modify: `scripts/build_kb.py`
- Modify: `app/core/kb_version.py`
- Modify: `app/main.py`
- Modify: `app/core/flywheel.py`
- Create: `tests/kb/test_faq_chunks.py`、`tests/kb/test_build_faq.py`
- Modify: `tests/api/test_review_api.py`

**Step 1: FAQ chunk**

`app/kb/loader.py` 增加：

```python
def faq_chunks(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": f"faq-{row['id']}",
            "text": f"{row['question']}\n{row['answer']}",
            "source": "faq",
            "section": row["question"],
            "kind": "faq",
        }
        for row in rows
    ]
```

**Step 2: `build_kb.py`**

- 读取 `faq` 行：`reviewed_at IS NOT NULL AND sync_status IN ('pending','synced')`；
- `chunks = load_chunks() + faq_chunks(rows)`；
- 用全部 chunks 计算 `kb_version`；
- upsert 到同一个 Chroma collection；
- 删除 stale ids；
- 成功后更新 FAQ `sync_status='synced'`、`kb_version=version`；
- 更新 canonical `status='written_back'`、`kb_ref=f"faq-{row.id}"`。

**Step 3: 同步入口**

`app/core/flywheel.py` 的 `approve` 在写完 FAQ 后调用
`sync_faq_to_kb()`（内部调用 build 逻辑，或 `subprocess` 跑脚本；优先抽成可复用函数，
不要在 API 里直接 `subprocess`）。

失败处理：

- 写 FAQ 成功、同步失败 → `faq.sync_status='pending'`，canonical `approved`，返回 502；
- 重试接口：再次调用 `approve` 或 `scripts/review.py retry <id>`；
- 成功后才 `written_back`。

**Step 4: `kb_version` 与 `/api/meta`**

- `kb_version` 包含 FAQ；
- `/api/meta` 返回包含 FAQ 的版本；
- 去掉 `cached_kb_version()` 的 `lru_cache`，或加显式 reload；
- 评测报告记录版本。

**Step 5: 测试**

- `faq_chunks` id/text/metadata 正确；
- build 把 FAQ 写进 Chroma（用 fake client / monkeypatch）；
- `kb_version` 在 FAQ 增加后变化；
- 同一问题补库后能命中 `faq-*`；
- stale FAQ 被删除；
- 同步失败可重试，状态不假通过。

**验证：**

```bash
uv run pytest tests/kb tests/api/test_review_api.py -q
uv run python scripts/build_kb.py
curl -s http://127.0.0.1:8090/api/meta | jq .kb_version
```

**Commit:** `feat(ch09): FAQ 写回并合并进知识库`

---

## Task 5: 入口补齐（可选）

**目的：** 让池子不只来自知识路弱证据。

**Files:**
- Create: `app/api/feedback.py`
- Modify: `app/main.py`
- Modify: `app/graph/rules_exit.py`
- Modify: `client/src/components/MessageBubble.tsx`（可选）
- Create: `tests/api/test_feedback_api.py`

**Step 1: 用户点踩 API**

```http
POST /api/feedback
{"conversation_id": 1, "message_id": 12, "rating": "down", "question": "..."}
```

- `down` → `record_low_conf(source='feedback', hits=None)`；
- `up` → 只记日志；
- 归属校验复用 Ch06。

**Step 2: 前端（可选）**

助手气泡下加 👍/👎；点踩调用 `/api/feedback`；一次性反馈。
如果 Ch08 前端不方便改，可以先不做，用 CLI 注入反馈样本验收。

**Step 3: 规则冲突**

`rules_exit` 在模型/规则分歧、转人工时调用：

```python
record_low_conf(
    question=query,
    source="rule_conflict",
    reason=f"rule_conflict:{rule_id}",
    hits=None,
)
```

**Step 4: 测试**

- down 落池，source 正确；
- up 不落库；
- 归属错误 404；
- 规则冲突样本进池。

**验证：**

```bash
uv run pytest tests/api/test_feedback_api.py -q
```

**Commit:** `feat(ch09): 点踩与规则冲突入口`

---

## Task 6: 补库前后评测 + 验收报告

**目的：** 证明飞轮不是“感觉变好了”。

**Files:**
- Create: `scripts/eval_flywheel.py`
- Modify/Create: `data/eval/flywheel_badcases.yaml`
- Create: `docs/reports/ch09-flywheel-acceptance.md`
- Create: `docs/reports/ch09-flywheel-changes.md`（补齐）

**Step 1: 评测脚本**

`scripts/eval_flywheel.py`：

```bash
uv run python scripts/eval_flywheel.py --phase before
uv run python scripts/eval_flywheel.py --phase after
```

流程：

1. 读取 `flywheel_badcases.yaml`；
2. 对每个 badcase 调用 `/api/eval/chat` 或直接复用 `run_turn`；
3. 计算 `hit@3` / `recall` / `citation_hit`；
4. 对 `out_of_kb` 计算 `refusal_rate`；
5. 输出 JSON + Markdown，记录 `kb_version`、`dataset_hash`、样本量；
6. 可选：调用 44 条确定性回归。

**Step 2: 补库前/后**

前置：评测实例要打开 `ENABLE_EVAL_API=true`。

```bash
# 1. 先跑 before（必须在尚无 flywheel FAQ 的库/集合上）
uv run python scripts/eval_flywheel.py --phase before

# 2. 审核通过 10 条 + build_kb
uv run python scripts/review.py list
uv run python scripts/review.py approve <id> --answer "..."
uv run python scripts/build_kb.py

# 3. 跑 after
uv run python scripts/eval_flywheel.py --phase after

# 4. 44 条回归
uv run python scripts/eval_e2e.py
```

**可复现性**：如果要重跑 `before`，先恢复 `data/app.db` 与 Chroma 集合到“无 flywheel FAQ”的快照
（或保留第一次 before 的结果）；不要在已经补库的库上重新跑 before，否则前后对比失真。

**Step 3: 验收线**

- badcase `hit@3` 上升；
- `out_of_kb_refusal` 不下降；
- 44 条确定性回归 44/44；
- `kb_version` before/after 不同；
- 报告中明确样本量与“离线模拟”边界。

**Step 4: 报告**

`docs/reports/ch09-flywheel-acceptance.md` 包含：

- 数据来源与样本量；
- before/after 指标表；
- 失败样本归因；
- 审核通过/驳回数量；
- 重复审批、同步失败、库外题不污染等边界测试；
- 复现命令。

**验证：**

```bash
uv run pytest -q
uv run ruff check .
uv run python scripts/eval_flywheel.py --phase after
```

**Commit:** `test(ch09): 飞轮补库前后评测与验收`

---

## Task 7: 审核 UI（可选）

**目的：** 如果要做演示，把 CLI 换成页面；不阻塞 Ch09 验收。

**Files:**
- Create: `client/src/pages/ReviewPage.tsx`（或独立 `client/review.html`）
- Modify: `client/src/main.tsx` / 路由
- Create: `client/src/components/ReviewDetail.tsx`
- Modify: `client/src/lib/api.ts`

**Step 1: 列表**

- `Table` 或 `List`：id、occurrence、standard question、suggested answer、status；
- 状态筛选：待审 / 通过 / 驳回；
- 按 occurrence 降序。

**Step 2: 详情**

- `Drawer`：归并原话、source、reason、召回快照；
- 快照三态展示：`NULL` / `[]` / 有召回。

**Step 3: 审核**

- `Modal + Form`：预填 suggested answer；
- 通过 → `POST /api/review/{id}/approve`；
- 驳回 → `POST /api/review/{id}/reject`；
- 失败显示 502/409 文案。

**验证：**

```bash
cd client
npm run typecheck
npm test
npm run build
```

**Commit:** `feat(ch09): 飞轮审核页面`

> 如果时间不够，Task 7 可以不做；Ch09 以 CLI 验收。

---

## Task 8: 文档、改动记录与收尾

**Files:**
- Modify: `docs/reports/ch09-flywheel-changes.md`
- Modify: `docs/reports/00-change-log-overview.md`
- Modify: `docs/reports/todo.md`
- Modify: `docs/reports/README.md`
- Modify: `docs/reports/resume-positioning.md`（只在验收后写）
- Modify: `README.md`（如需要，写“离线模拟飞轮”边界）

**Step 1: 改动记录**

每个 task 追加：
- 新增文件；
- 修改与原因（旧逻辑 / 新逻辑 / 为什么旧的撑不住）；
- 验证命令与结果。

**Step 2: 更新索引**

- `00-change-log-overview.md` 登记 Ch09；
- `docs/reports/README.md` 增加验收报告链接；
- `todo.md` 把飞轮项从“待做”改为“已做/边界”；
- `resume-positioning.md` 只在**真正完成后**写“离线模拟飞轮”，不要写“线上飞轮”。

**Step 3: 最终验证**

```bash
uv run pytest -q
uv run ruff check .
uv run python scripts/init_db.py
uv run python scripts/migrate_ch09.py --db data/app.db
uv run python scripts/migrate_ch09.py --db data/eval.db
cd client && npm run typecheck && npm test && npm run build
```

**Commit:** `docs(ch09): 飞轮验收与索引更新`

---

## 验收清单

- [ ] 迁移幂等；新库/老库都能跑。
- [ ] 低置信问题能进池；`retrieved` 三态正确。
- [ ] 批处理能新建 canonical、合并同义、累加 occurrence。
- [ ] 驳回终态；同义问题不复活。
- [ ] approve 写 FAQ；重复 approve 不重复写。
- [ ] FAQ 写回后 `build_kb.py` 能检索命中 `faq-*`。
- [ ] 同步失败保持 `approved/pending`，可重试，不假 `written_back`。
- [ ] `kb_version` 包含 FAQ，`/api/meta` 反映变化。
- [ ] 补库前/后 badcase `hit@3` 上升。
- [ ] 库外题拒答率不下降。
- [ ] Ch07 44 条确定性回归仍为 44/44。
- [ ] 文档明确“离线模拟飞轮，无真实用户流量”。
- [ ] 全量测试与 ruff 通过。

---

## 建议执行顺序

```
Task 0 基线/fixture
  → Task 1 迁移
  → Task 2 批处理
  → Task 3 审核 CLI/API
  → Task 4 写回 + 检索
  → Task 6 评测/验收（先不做 Task 5）
  → Task 5 点踩/规则冲突（可选）
  → Task 7 UI（可选）
  → Task 8 文档收尾
```

**先做 Task 0–4 + Task 6**，就能证明飞轮闭环；Task 5/7 是增强项，不要阻塞验收。
