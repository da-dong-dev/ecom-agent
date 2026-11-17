# Ch09 数据飞轮 · 改动记录

> 只增不改：记录每个 Task 的产出、为什么这样做、怎么验证。
> 当前状态：Task 0–3 完成，尚未提交。

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
