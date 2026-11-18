# Ch09 数据飞轮 · 改动记录

> 只增不改：记录每个 Task 的产出、为什么这样做、怎么验证。
> 当前状态：Task 0–8 完成，尚未提交。

---

## 0 · Task 0 · 基线核对 + 合成 badcase fixture（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `data/eval/flywheel_badcases.yaml` | 12 条知识库未覆盖的 badcase + 10 条库外题；离线模拟，无真实个人信息 |
| `docs/reports/ch09-flywheel-changes.md` | 本文件；Ch09 改动记录起点 |

### 基线

日期：2026-11-17。

| 项 | 结果 |
|---|---|
| 后端测试 | `1085 passed, 9 skipped` |
| Ruff | `All checks passed!` |
| 建表 / schema 漂移检查 | 10 张表；`schema.sql 与 ORM 一致（表 / 列 / 索引）` |
| `kb_version` | `80bd26573951` |
| Chroma 集合 | `ecom_kb`，`space=cosine`，57 个 chunk |
| 前端（参考） | 11 个测试文件 / 78 条测试；构建通过 |

> 与 [ch08-acceptance.md](./ch08-acceptance.md) 的 `1084 passed, 10 skipped` 相差 1 条：
> 当前 `-rs` 显示 8 条 docs consistency skip + 1 条 gold skip；
> 这是文档任务状态变化导致的 skip 条件变化，不是代码回归。基线以本次实测为准。

### Fixture 设计

`badcases`（12 条）覆盖当前语料没有回答的售后政策缺口：

| 类别 | 例子 | 期望关键词 |
|---|---|---|
| 价保 | 价保规则是什么 | 价保 / 补差 |
| 售后能力 | 支持以旧换新吗 | 以旧换新 / 不支持 |
| 配送变更 | 修改收货地址的规则是什么、可以指定顺丰吗、大件商品配送上楼规则是什么 | 地址 / 快递 / 上楼 |
| 支付方式 | 支持货到付款吗 | 货到付款 / 不支持 |
| 线下服务 | 线下门店试坐政策是什么 | 门店 / 试坐 |
| 发票异常 | 纸质发票寄丢了怎么办 | 发票 / 丢失 |
| 运费险时效 | 运费险理赔多久到账 | 运费险 / 工作日 |
| 预售定金 | 预售商品定金能退吗 | 定金 / 尾款 |
| 会员权益 | 生日券没收到怎么补 | 生日券 / 补发 |
| 订单拆分 | 订单可以拆开发货吗 | 拆单 / 发货 |

`out_of_kb`（10 条）是明确不在当前商品/政策范围内的题，用于检查补库后拒答率不下降：

```text
成人轮椅 / 安装安全座椅 / 花呗分期 / 线下维修点 / 代购其他品牌
定制婴儿床 / 宠物推车 / 跨境电商直邮 / 刻宝宝名字 / 招加盟商
```

**边界**：这些是合成问题，答案由后续人工审核补写；Task 0 不写答案、不改生产代码。
所有报告必须标注“离线模拟飞轮，无真实用户流量”。

> 实跑核对（临时库）：F01/F02/F04/F12 会直接触发
> `retrieval_low_conf` 落池；其余问题虽然也没被正确回答，但不一定走到弱证据池。
> 因此 Task 2 的 `--seed-fixtures` 仍负责把整份 fixture 统一插入池子，
> 保证 before/after 用同一批题，而不是依赖线上分类的偶然性。

### 验证

```bash
# 0. 文档一致性（新增 Ch09 记录后）
uv run pytest tests/docs -q
# 71 passed, 8 skipped

# 1. fixture 结构与数量
uv run python - <<'PY'
import yaml
d = yaml.safe_load(open("data/eval/flywheel_badcases.yaml"))
assert 10 <= len(d["badcases"]) <= 20
assert len(d["out_of_kb"]) >= 10
print("badcases", len(d["badcases"]), "out_of_kb", len(d["out_of_kb"]))
PY
# badcases=12 out_of_kb=10

# 2. 用临时库验证落池入口，不污染 data/app.db
DATABASE_URL=sqlite+aiosqlite:////tmp/ch09-task0.db uv run python - <<'PY'
# create_all → record_low_conf(hits=[]) × 12 → count
PY
# inserted: 12
# retrieved sample: []
```

### 关联

- 前置：[Ch08 验收](./ch08-acceptance.md) 已完成；本 Task 不依赖 Ch08 断流重连。
- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) D-F1/D-F2/D-F8。
- 下一步：Task 1（`suggested_answer` + 索引 + 迁移）。

---

## 1 · Task 1 · 数据层：`suggested_answer` + 索引 + 迁移（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `scripts/migrate_ch09.py` | 幂等迁移：补 `suggested_answer`、两个查询索引、FAQ 唯一部分索引 |
| `tests/db/test_migrate_ch09.py` | 老库补列/补索引、幂等、缺表报错 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/db/models.py › LowConfQuestion` | 只有 `normalized` | 增加 `suggested_answer`（可空） | Task 2 标准化后要存模型建议答案，人工审核时参考；不写回原话 |
| `app/db/models.py › LowConfQuestion.__table_args__` | 无查询索引 | `idx_low_conf_status`、`idx_low_conf_merged_into` | 审核队列按状态筛、查重按 merged_into 排除已归并行；数据量上来后不能全表扫 |
| `app/db/models.py › Faq.__table_args__` | 无唯一约束 | `uk_faq_source_question` 部分唯一索引 | 同一 canonical 重复 approve/重试会写出多条 FAQ；唯一索引兜底 |
| `app/db/schema.sql` | 无新列/索引 | 同步 ORM | `schema.sql` 是可审阅 DDL，和 ORM 必须一致 |
| `scripts/init_db.py` | 已有 schema 漂移检查 | **未改** | 新索引已由现有检查覆盖，不需要额外逻辑 |

### 迁移行为

```text
旧库：
  low_conf_questions.suggested_answer 不存在 → ALTER TABLE ADD COLUMN
  idx_low_conf_status 不存在 → CREATE INDEX
  idx_low_conf_merged_into 不存在 → CREATE INDEX
  uk_faq_source_question 不存在 → CREATE UNIQUE INDEX ... WHERE flywheel
重复执行：
  列/索引都在 → 返回 False，打印“已是最新”
```

`uk_faq_source_question` 只约束：

```sql
source_type = 'flywheel' AND source_question_id IS NOT NULL
```

所以 `manual` FAQ 不受影响，可以有多条相同/空 `source_question_id`。

### 验证

```bash
uv run pytest tests/db/test_migrate_ch09.py tests/db/test_models.py -q
# 12 passed

uv run python scripts/init_db.py
# 10 张表；schema.sql 与 ORM 一致（表 / 列 / 索引）

uv run pytest -q
# 1091 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

实际库迁移（迁移前已备份到 `/tmp/app.db.before-ch09`、`/tmp/eval.db.before-ch09`）：

```text
data/app.db: 已迁移 → 已是最新
data/eval.db: 已迁移 → 已是最新
```

迁移后核对：

```text
data/app.db:  suggested_answer 存在；3 个索引存在；low_conf 8 行、faq 0 行
data/eval.db: suggested_answer 存在；3 个索引存在；low_conf 3 行、faq 0 行
```

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) §3.1/§3.2。
- 下一步：Task 2（标准化 + 查重批处理 + `--seed-fixtures`）。

---

## 2 · Task 2 · 标准化 + 查重批处理（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/flywheel.py` | 标准化、候选匹配、embedding 二次校验、批处理、统计 |
| `scripts/flywheel.py` | CLI：`--seed-fixtures` / `--limit` / `--dry-run` |
| `tests/core/test_flywheel.py` | 9 条：新建、合并、驳回、幻觉 id、异常、同批同义、dry-run、seed |
| `app/core/prompts.py › FLYWHEEL_NORMALIZE_*` | 标准化 prompt + function schema |

### 处理流程

```text
取 pending + normalized IS NULL
  → LLM 标准化 + 选候选 matched_id
  → matched_id 不在候选 → skip
  → 有 matched_id → embedding 相似度 >= 0.9 → merge
  │                    └─ < 0.9 → 新建 canonical
  → 无 matched_id → 精确标准问题匹配 → merge 或新建
  → 命中：occurrence 原子 +1，原话 status=merged
  → 未命中：写 normalized/suggested_answer，保持 pending
  → 任一步异常：rollback 当前行，skip，下一轮可重试
```

关键点：

- `matched_id` 只能来自候选；不存在直接 `skip`，不猜；
- embedding 二次校验阈值 `0.9`，防止 LLM 误合并；
- 精确标准问题匹配覆盖“同批两条同义”的情况；
- `rejected` 候选可被命中、只累加 `occurrence`，不改回 `pending`；
- `occurrence` 用 SQL `occurrence = occurrence + 1` 原子更新；
- `--dry-run` 调模型但不写库；同批新建 canonical 不进入后续候选，统计只作预览。

### 验证

```bash
uv run pytest tests/core/test_flywheel.py -q
# 9 passed

uv run pytest -q
# 1100 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

真实 CLI（临时库 `/tmp/ch09-task2.db`，不污染 `data/app.db`）：

```text
--seed-fixtures → inserted=12 skipped=0
--limit 1 --dry-run → processed=1 merged=0 created=1 skipped=0
--limit 1 → processed=1 merged=0 created=1 skipped=0
--limit 20 → processed=11 merged=0 created=11 skipped=0
```

12 条 fixture 都生成了 canonical，`suggested_answer` 为空字符串时落库为 `NULL`。
本批问题互不重复，所以没有触发 merge；merge 路径由测试覆盖。

### 边界

| 边界 | 说明 |
|---|---|
| 模型建议答案为空 | 没有知识依据时不臆造，人工审核时补答案 |
| embedding 服务失败 | 当前行 skip，不改数据；修复服务后重跑 |
| 候选只取 200 条 | 够 MVP；数据量上来后要换召回/分页 |
| 同批 dry-run | 不写库，新建 canonical 不会进入后续候选 |
| 写 FAQ / 审核 | 不做；Task 3/4 |

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) D-F6、§3.3、§4。
- 下一步：Task 3（审核 CLI/API）。

---

## 3 · Task 3 · 审核服务 + CLI/API（2026-11-18）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/review.py` | 队列、详情、approve、reject；统一错误类型 |
| `app/api/review.py` | 4 个审核接口；`ENABLE_REVIEW_API` 开关 |
| `scripts/review.py` | CLI：list / show / approve / reject |
| `tests/core/test_review_service.py` | 11 条状态机与详情测试 |
| `tests/api/test_review_api.py` | 6 条 HTTP 映射与开关测试 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/main.py` | 只注册 eval/conversations | 注册 review router | 审核 API 不注册就不存在 |
| `app/config.py` | 只有 `enable_eval_api` | 增加 `enable_review_api=False` | 审核会写 FAQ，不能默认暴露 |
| `.env.example` | 只有 `ENABLE_EVAL_API` | 增加 `ENABLE_REVIEW_API=false` | 本地演示时知道要打开哪个开关 |

### 服务语义

```text
list_queue(status=None)
  → 默认只返回 pending canonical
  → status 可切到 approved / rejected / written_back
  → 按 occurrence DESC, id DESC

get_detail(id)
  → canonical + 归并原话 + retrieved 快照
  → retrieved 三态保留：NULL / [] / [...]

approve(id, answer)
  → 空答案 422
  → 非 pending 409
  → 写 faq(pending) + canonical.status=approved
  → 重复 approve 409，不重复写 FAQ

reject(id, reason)
  → 非 pending 409
  → canonical.status=rejected（终态）
```

`retrieved` 快照通过 `load_chunks()` 把 id 映射回 `section/text/source`，审核时能直接看到“当时召回了什么”。

### 边界

| 边界 | 说明 |
|---|---|
| 驳回原因 | 目前只在响应/日志里返回，表里没有 `review_reason` 字段，不落库 |
| KB 同步 | approve 只写 `faq(sync_status=pending)`；向量化是 Task 4 |
| 并发 approve | 状态检查 + `uk_faq_source_question` 唯一索引兜底 |
| API 默认关闭 | `ENABLE_REVIEW_API=false` 时所有审核接口 404 |

### 验证

```bash
uv run pytest tests/core/test_review_service.py tests/api/test_review_api.py -q
# 17 passed

uv run pytest -q
# 1117 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

真实 CLI（临时库 `/tmp/ch09-task2.db`）：

```text
list → 12 条 pending canonical
show 1 → canonical + retrieved=[] + merged_questions=[]
approve 1 --answer "..." → status=approved，faq_id=1
reject 2 --reason "暂不补库" → status=rejected
重复 approve 1 → [失败] 当前状态不可通过：approved，exit=1
```

数据库核对：

```text
low_conf_questions id=1 status=approved，reviewed_at 非空
faq id=1 question=价保规则是什么，source_type=flywheel，sync_status=pending
low_conf_questions id=2 status=rejected
```

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) §3.3、§4.4。
- 下一步：Task 4（FAQ 写回 + KB 合并 + 检索验证）。

---

## 4 · Task 4 · FAQ 写回 + KB 合并 + 检索验证（2026-11-18）

### 新增

| 文件 | 内容 |
|---|---|
| `app/kb/build.py` | 可复用的 build：静态语料 + FAQ → 向量化 → Chroma → 更新状态 |
| `tests/kb/test_faq_chunks.py` | FAQ chunk 形状 |
| `tests/kb/test_build_faq.py` | 版本、upsert、stale 删除、失败不假成功 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/kb/loader.py › faq_chunks` | 只加载静态 Markdown | 增加 FAQ → chunk 转换 | FAQ 必须和文档同构，才能复用同一套 upsert/删除 |
| `app/kb/build.py › build_kb` | 逻辑只在脚本里 | 抽成可复用函数，返回版本/数量 | 审核通过后要同步，不能在 API 里 `subprocess` |
| `scripts/build_kb.py` | 自己实现全部构建 | 只做 CLI 包装 | 避免 CLI 与审核同步两套逻辑漂移 |
| `app/core/kb_version.py › current_kb_version` | 只哈希静态语料 | 静态语料 + 已审核 FAQ | `/api/meta` 必须反映 FAQ 写回后的版本 |
| `app/main.py › /api/meta` | `cached_kb_version()` | `await current_kb_version()` | 旧缓存看不到 FAQ，且 lru_cache 会掩盖变化 |
| `app/core/review.py › approve` | 只写 FAQ，状态 approved | 写 FAQ 后调用 `build_kb`，成功后 written_back | 审核通过不等于检索可用；必须向量化成功 |
| `app/core/review.py › retry_sync` | 无 | approved 的 FAQ 可重试同步 | Chroma/Ollama 失败后不能要求重新审核 |
| `app/api/review.py` | 无 retry / 502 | 增加 retry 路由与 `sync_failed=502` | 同步失败要可重试，不能返回 200 假成功 |
| `scripts/review.py` | 无 retry | 增加 `retry` 子命令 | CLI 与 API 能力对齐 |

### 同步顺序

```text
approve
  → 写 faq(sync_status=pending)
  → canonical.status=approved
  → commit
  → build_kb(session=同一个 session)
       → 静态 chunks + FAQ chunks
       → Chroma upsert / 删除 stale
       → faq.sync_status=synced, faq.kb_version=version
       → canonical.status=written_back, kb_ref=faq-{id}
       → commit
  → 返回 status=written_back
```

失败时：

```text
build_kb 失败
  → canonical 仍是 approved
  → faq 仍是 pending
  → API 502 sync_failed
  → scripts/review.py retry <id> 重试
```

### `retrieved` / 检索

FAQ chunk：

```text
id      = faq-{faq.id}
text    = question + "\n" + answer
source  = "faq"
section = question
kind    = "faq"
```

`build_kb` 的 stale 删除覆盖 FAQ：如果 FAQ 行被停用/删除，下一次 build 会把
`faq-*` 从 Chroma 删除。

### 验证

```bash
uv run pytest tests/kb tests/core/test_review_service.py tests/api/test_review_api.py -q
# 28 passed

uv run pytest -q
# 1128 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

真实构建（临时库 + 隔离集合 `ecom_kb_ch09_test`）：

```text
chunks=58  faq=1  kb_version=db11667deb27
检索「价保规则是什么」→ faq-1 第 1，similarity=0.6171
canonical id=1 → written_back，kb_ref=faq-1
faq id=1 → synced，kb_version=db11667deb27
```

真实审核同步（临时库 + 隔离集合 `ecom_kb_ch09_test2`）：

```text
approve id=3
→ status=written_back，faq_id=2，kb_version=96c3c30eecd6
检索「修改收货地址的规则是什么」→ faq-2 第 1，similarity=0.8146
```

两个临时集合已删除；未污染正式 `ecom_kb`。

### 边界

| 边界 | 说明 |
|---|---|
| 全量重建 | 每次 approve 都重建全部 chunk；MVP 可接受，数据量大后要增量 |
| 同步失败 | canonical 保持 approved、FAQ pending；不假装 written_back |
| 手工 FAQ | 会被合并进 KB，但没有 canonical，不会改低置信池状态 |
| 版本口径 | `current_kb_version` 与 build 使用同一过滤条件 |
| 审核驳回原因 | 仍只回显/记日志，不落库（Task 3 边界） |

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) §4.5、D-F11。
- 下一步：Task 5（可选：点踩/规则冲突）或 Task 6（补库前后评测）。按计划先做 Task 6。

---

## 5 · Task 5 · 点踩与规则冲突入口（2026-11-18）

### 新增

| 文件 | 内容 |
|---|---|
| `app/api/feedback.py` | `POST /api/feedback`：点踩落池、点赞只记日志 |
| `tests/api/test_feedback_api.py` | 7 条：down/up、幂等、归属、消息、空问题 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/main.py` | 没有反馈路由 | 注册 feedback router | C 端需要有明确的“答得不好”入口 |
| `app/graph/rules_exit.py › compliance` | 分歧只写 span + 转人工 | 额外落 `low_conf_questions(source='rule_conflict')` | 模型与规则分歧是知识缺口/规则冲突的高价值样本，不能只留在 trace 里 |
| `tests/graph/rules/test_transfer_paths.py` | 集成测试会走真实落池 | monkeypatch `record_low_conf` 为 no-op | 测试不应写 `data/app.db` |
| `tests/graph/rules/test_compliance.py` | 只断言分歧转人工 | 新增“分歧落池”断言 | 防止只转人工、不沉淀样本 |

### 反馈语义

```text
POST /api/feedback
{"conversation_id": 1, "message_id": 12, "rating": "down", "question": "..."}

down：
  → 校验会话归属（Ch06）
  → 校验 message 属于该会话且是 assistant
  → question 为空时取最后一条 user 消息
  → 写 low_conf_questions(source='feedback', hits=NULL, reason='feedback_down:{message_id}')
  → 同一 message 重复点踩幂等

up：
  → 只记日志，不落库
```

规则冲突：

```text
compliance 发现 rule_divergence
  → record_low_conf(source='rule_conflict', hits=None)
  → reason = "rule_conflict:rule_divergence: ..."
  → conversation_id 一并带上
```

`hits=None` 表示“不是检索弱证据”，审核详情里 `retrieved` 为 `NULL`；不要把它和知识缺口 `[]` 混在一起统计。

### 未做（可选）

前端 👍/👎 按钮没有加。当前先用 API / CLI 注入反馈样本；Task 7 审核 UI 也仍是可选。
如果后续要做，前端只需在助手气泡下加两个按钮，调用 `/api/feedback`。

### 验证

```bash
uv run pytest tests/api/test_feedback_api.py tests/graph/rules/test_compliance.py -q
# 37 passed（含原有 compliance / transfer 测试）

uv run pytest -q
# 1136 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

### 边界

- 反馈接口默认启用；当前没有真实登录，归属仍依赖 `demo_user_id`。
- 同一消息重复点踩幂等；不同消息同一问题会各落一条（保留上下文）。
- `up` 不落库，避免把正向反馈混进低置信池。
- 规则冲突落池失败只记日志，不影响转人工。

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) §4.2、D-F8。
- 下一步：Task 6（补库前后评测）。

---

## 6 · Task 6 · 补库前后评测 + 验收报告（2026-11-18）

### 新增

| 文件 | 内容 |
|---|---|
| `scripts/eval_flywheel.py` | before/after 评测：hit@3、recall、citation_hit、refusal_rate |
| `tests/eval/test_eval_flywheel.py` | 5 条纯指标函数测试 |
| `docs/reports/ch09-flywheel-before.{json,md}` | before 原始结果 |
| `docs/reports/ch09-flywheel-after.{json,md}` | after 原始结果 |
| `docs/reports/ch09-flywheel-regression-44.md` | 44 条确定性回归 |
| `docs/reports/ch09-flywheel-acceptance.md` | 验收报告 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `app/core/prompts.py › INTENT_SYSTEM` | 多意图只看“有没有两个诉求” | 补充“同一售后目标的多个条件/动作不算多意图” | B07「已发货未收到 + 取消退款」被误判成多意图，44 条回归掉到 43/44 |
| `tests/graph/test_intent.py` | 只有两条多意图正例 | 增加 B07 的单一意图回归 | 防止 prompt 调整把“退款条件多”又标成多意图 |

### 评测口径

```text
hit@3      = 前 3 条召回 chunk 含任一期望关键词
recall     = 答案中出现期望关键词的比例
citation   = 回答引用里是否出现 faq-*
refusal    = route=human/fallback、transfer 动作、或明确拒答话术
```

同一份 fixture（`dataset_hash=fca8f2d1fd4b`），只改变知识库：

| 指标 | before | after |
|---|---:|---:|
| hit@3 | 0.500 | **1.000** |
| recall | 0.667 | **0.958** |
| citation_hit | 0.000 | **1.000** |
| refusal_rate | 0.700 | **0.800** |
| kb_version | `80bd26573951` | `865da1a0b66e` |

44 条确定性回归：

```text
通过 44/44（100.0%）
```

### 验证

```bash
uv run pytest tests/eval/test_eval_flywheel.py -q
# 5 passed

uv run pytest -q
# 1142 passed, 9 skipped

uv run ruff check .
# All checks passed!
```

真实 before/after（临时库 + 两个隔离 Chroma 集合）：

```text
before: hit@3=0.500 recall=0.667 citation_hit=0.000 refusal_rate=0.700
after:  hit@3=1.000 recall=0.958 citation_hit=1.000 refusal_rate=0.800
```

临时集合 `ecom_kb_ch09_before` / `ecom_kb_ch09_after` 已删除，正式 `ecom_kb` 未受影响。

### 边界

| 边界 | 说明 |
|---|---|
| 样本量 | 12 条 badcase + 10 条库外题，不是线上统计 |
| 答案来源 | 人工补写的合成答案，不是真实平台政策 |
| F07 recall=0.50 | 生成用「寄丢」，期望词是「丢失」；检索/引用都命中 |
| O01/O07 | 补库前后都未拒答，属于既有 data 路由问题，与飞轮无关 |
| 审核 UI | Task 7 可选未做 |

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) D-F8、§6。
- 验收：[ch09-flywheel-acceptance.md](./ch09-flywheel-acceptance.md)。
- 下一步：Task 7（可选审核 UI）或 Task 8 收尾。

---

## 7 · Task 7 · 审核 UI（可选，2026-11-18）

### 新增

| 文件 | 内容 |
|---|---|
| `client/src/pages/ReviewPage.tsx` | 审核列表、详情 Drawer、通过/驳回 Modal、重试同步 |
| `docs/assets/ch09/01-review-queue.png` | 审核队列截图 |
| `docs/assets/ch09/02-review-detail.png` | 详情 Drawer 截图 |
| `client/src/lib/review-api.test.ts` | 4 条审核 API 请求测试 |

### 修改与原因

| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `client/src/main.tsx` | 只渲染聊天 App | `#review` 渲染 ReviewPage | 不引入 router，内部页用 hash 隔离，C 端聊天不变 |
| `client/src/lib/api.ts` | 只有会话接口 | 审核队列/详情/approve/reject/retry | 审核页需要独立 API 封装 |
| `client/src/lib/base.ts` | 409/422/502 统一 internal_error | 增加 conflict/invalid_arg/sync_failed，并解析 `detail.message` | 审核失败要显示“同步失败/状态不可流转”，不能只说服务不可用 |
| `client/src/styles.css` | 只有聊天样式 | 审核页背景、卡片、详情/命中块 | 内部页也需要可读的布局 |

### 页面能力

```text
/#review
  → 状态 Tab：待审核 / 已通过 / 已写回 / 已驳回
  → 表格：id、出现次数、标准问题+原话、建议答案、状态、操作
  → 详情 Drawer：
      - 标准问题 / 原话 / 来源 / 原因 / 建议答案
      - 检索快照三态：NULL / [] / [...]
      - 归并原话列表
  → pending：通过并写回 / 驳回
  → approved：重试同步
  → API 关闭（404）：提示设置 ENABLE_REVIEW_API=true
```

不引入 `react-router`：`#review` 只影响入口渲染，聊天页仍走 `/`。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 12 files, 84 tests passed
npm run build       # 通过；dist JS 1118.13 kB（gzip 349.91 kB）
```

Playwright 实机检查：

```text
审核队列：10 条 pending
详情 #12：检索零命中（[]）、无归并原话
通过 Modal：建议答案预填、空答案会拦截
```

### 边界

| 边界 | 说明 |
|---|---|
| 权限 | 没有登录/角色，审核页是本地内部页 |
| 分页 | MVP 不做分页；队列最多返回当前状态全部 canonical |
| 审核 API | 默认关闭；需 `.env` 设置 `ENABLE_REVIEW_API=true` |
| 截图 | 只作内部演示，不代表生产 UI |

### 关联

- 设计依据：[ch09-flywheel-design.md](../superpowers/specs/ch09-flywheel-design.md) D-F7。
- 下一步：Task 8 文档收尾（本文已完成）。

---

## 9 · C 端反馈按钮（Task 5 补做，2026-11-18）

### 新增/修改

| 文件 | 改动 |
|---|---|
| `client/src/components/MessageBubble.tsx` | 助手气泡下增加「有帮助 / 没帮助」 |
| `client/src/components/ChatWindow.tsx` | 透传 feedback 状态与回调 |
| `client/src/hooks/useChatSession.ts` | `handleFeedback`：找上一条 user 问题、调 `/api/feedback`、本地置灰 |
| `client/src/App.tsx` | 把 `feedbackByMessage` / `handleFeedback` 接到 ChatWindow |
| `client/src/lib/api.ts` | `sendFeedback` |
| `client/src/styles.css` | 反馈行样式 |
| `client/src/components/message-bubble.test.tsx` | 3 条反馈 UI 测试 |
| `client/src/lib/feedback-api.test.ts` | 1 条请求体测试 |
| `docs/assets/ch09/03-feedback-buttons.png` | 截图 |

### 交互

```text
助手消息 id 存在 + 不在生成中
  → 显示「这个回答有帮助吗？ 👍 有帮助 / 👎 没帮助」
  → 点击后调 POST /api/feedback
  → 本地点完置灰，显示「感谢反馈」
  → 切换会话时清空本地 feedback 状态
```

- `down` 落池 `source=feedback`、`hits=NULL`；要等 Task 2 批处理标准化后才进审核队列；
- `up` 后端只记日志，不落库；
- 用户消息、in-flight 草稿、没有 id 的消息不显示按钮。

### 验证

```bash
cd client
npm run typecheck   # 通过
npm test            # 13 files, 88 tests passed
npm run build       # 通过；dist JS 1121.87 kB（gzip 351.20 kB）
```

Playwright 截图：`docs/assets/ch09/03-feedback-buttons.png`。

点击验证（feedback API 用 mock，未写真实库）：

```json
{"conversation_id":57,"message_id":254,"rating":"down","question":"订单1002 不想要了"}
```

### 边界

- 反馈状态只在前端内存；刷新后按钮恢复可点，后端按 message_id 幂等；
- 没有登录，归属仍依赖 `demo_user_id`；
- 点踩样本是原始 pending 行，必须跑 Task 2 批处理才会成为 canonical。
