# 新窗口交接：从 Ch08 继续

> 用途：新开对话窗口时，把这份文件发给助手，作为项目背景。
> 详细设计仍以 `docs/superpowers/` 和 `docs/design/` 为准。

## 一句话背景

个人项目：母婴电商客服 Agent，按章节递进实现。
**Ch01–Ch07 已完成并推送；Ch08 前端（聊天 + 进度面板）的 spec 已写，plan 还没写。**

## 仓库与路径

| 内容 | 路径 |
|---|---|
| 主项目 | `/Users/lidong/Desktop/workFile/ecom-agent` |
| 评测平台 | `/Users/lidong/Desktop/workFile/evalflow` |
| 设计文档 | `docs/design/电商客服-*.md` |
| 章节 spec | `docs/superpowers/specs/chNN-*.md` |
| 章节 plan | `docs/superpowers/plans/chNN-*.md` |
| 运行/评测报告 | `docs/reports/` |

两个仓库的 `main` 都已和 `origin/main` 同步。

## 当前状态

- Ch01 数据地基 ✅
- Ch02 主流程骨架 / 路由 / 检索 / 证据闸 ✅
- Ch03 C 出口（退款）✅
- Ch04 B 出口（数据查询）✅
- Ch05 多轮 / 锁 / 只读 ReAct ✅
- Ch06 安全 ✅
- Ch07 评测 ✅（验收见 `docs/reports/ch07-acceptance.md`）
- Ch08 前端：**spec 已写（C2-lite），plan 待写；实现未开始**

验证基线：

- ecom 全量：**1030 passed, 16 skipped**
- `ruff check .`：通过
- evalflow 全量：**127 passed**
- Ch07 确定性回归：**44/44**
- 当前质量快照：run-03，correctness 0.9977 / recall 1.00

## 先读哪些文档

按顺序：

1. `docs/reports/project-review.md`：主线图、Ch02/03/05 四格卡、决策地图、三个故事；
2. `docs/reports/ch07-overview.md`：Ch07 全章导读；
3. `docs/superpowers/specs/00-chapters-overview.md`：章节划分；
4. `docs/superpowers/plans/00-overview.md` 的 P8：Ch08 范围；
5. `docs/design/电商客服-意图与出口设计.md`：5 出口；
6. 需要时再看各章 spec。

## Ch08 的范围（来自 P8）

- 8.1 聊天窗 + SSE 流式
- 8.2 进度面板（核对中 / 已算出 / 待确认）
- 8.3 转人工入口
- 8.4 断流重连（补历史再继续）

验收：四个演示场景在界面上能走通。

**注意**：Ch08 的 spec 已经存在（2026-11-13）：

`docs/superpowers/specs/ch08-frontend-design.md`

它已经按当前代码核对了 C2-lite 的前置条件，并列出 4 个开放问题（见 §14）。
所以下一步不是写 spec，而是：

1. 先确认 §14 的 4 个问题；
2. 再写 `docs/superpowers/plans/ch08-frontend.md`；
3. 拆 task，逐 task 实现。

不要直接开写代码。

## 需要带进 Ch08 的已知边界

| 边界 | 说明 |
|---|---|
| D 出口不建工单 | 当前只发 `transfer_human` 动作；`create_ticket` 设计过但未实现 |
| 定作 + 已发货未签收 | 当前代码/评测按 `reject_then_refund`；真实业务更可能转人工/扣定制费，属于未决业务口径 |
| 退款流水线低于模型 | 差在 `condition_ok` / `packaging_opened` 只有订单级，不是模型错 |
| held-out 非严格盲测 | 运行后修正过 gold，且之前看过结果 |
| B5–B7 relevance 低 | 安全兜底被通用 judge 判成不相关，不按普通问答解读 |

## 工作约定

- **不自动 commit / push**：提交和推送必须由用户明确指示；
- **spec / plan 同步**：代码改动要同步回 spec 和 plan；
- **改动清单**：每个任务完成后列出新增 / 修改 / 删除的文件 + 验证结果；
- **术语解释**：专有名词首次出现要附一句白话；
- 用户是前端转 AI，偏好看具体代码和“为什么这样设计”，不要只给结论；
- 当前处于“复习 + 开发并行”阶段：Ch02/03/05 已复盘，Ch01/04/06 待补；做 Ch08 时用“功能驱动复习”。

## 常用命令

```bash
# ecom 测试
cd /Users/lidong/Desktop/workFile/ecom-agent
uv run pytest -q

# ecom 评测实例
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run uvicorn app.main:app --port 8091

# 确定性 44 条
uv run python scripts/eval_e2e.py \
  --base-url http://127.0.0.1:8091 \
  --out docs/reports/run-local-06.md

# EvalFlow（另开仓库）
cd /Users/lidong/Desktop/workFile/evalflow
EVAL_TARGET=ecom ECOM_BASE_URL=http://127.0.0.1:8091 \
  uv run uvicorn backend.main:app --port 8002
EVAL_TARGET=ecom ECOM_BASE_URL=http://127.0.0.1:8091 \
  uv run celery -A backend.celery_app worker --pool=threads --concurrency=2
```

依赖服务：Chroma（Docker，8000）、Redis（6379）、Ollama（11434，bge-m3）。
细节见 `docs/reports/local-env.md`。

## 建议的第一句话（可直接复制）

> 我在继续 `/Users/lidong/Desktop/workFile/ecom-agent` 这个项目。
> 先读 `docs/reports/handoff-ch08.md` 和 `docs/reports/project-review.md`，
> 了解 Ch01–Ch07 的状态与边界。
> Ch08 的 spec 已经有了（docs/superpowers/specs/ch08-frontend-design.md），
> 先确认 §14 的 4 个开放问题，再写 plan，不要直接写代码。
