# TODO 总表

> 更新：2026-11-16
> 用途：把散在 plan / design / spec / acceptance 里的“待做、已知边界、触发条件”收成一张表。
> 原则：**P0 是当前阻塞项；P1 是产品缺口；P2 是评测边界；P3 是技术债；P4 是触发后再做的优化。**

---

## P0 · Ch08 阻塞项（已关闭）

> 2026-11-16：P0-1 / P0-2 全部关闭；四场景与断流验收见
> [ch08-acceptance.md](./ch08-acceptance.md)。

来源：`docs/superpowers/specs/ch08-frontend-design.md` §0 / §11 / §14

### P0-1 需要先确认的 4 个开放问题

| # | 问题 | 建议 |
|---|---|---|
| 1 | 保存失败时是否接受 fail-closed（宁可不展示，也不展示刷新后会消失的回答） | ✅ 已确认：接受；与 C2-lite 的“看到即已存”一致 |
| 2 | 是否用唯一部分索引 `uk_msg_reply` 强约束“一条用户消息最多一条助手回复” | ✅ 已确认：接受；并发重试靠 DB 兜底 |
| 3 | 会话列表只做只读最近 20 条，不做搜索/重命名/删除 | ✅ 已确认：接受；范围控制 |
| 4 | `subgraphs=True` 接入成本高时，是否退化为 route + 计时提示 | ✅ Task 0 spike 通过：不降级，走真实 `progress` |

### P0-2 后端要先补的契约

| # | 项 | 现状 | 备注 |
|---|---|---|---|
| 1 | `GET /api/conversations` | ✅ Task 4 已完成 | 只读最近 20 条，`user_id` 过滤 |
| 2 | `GET /api/conversations/{id}/messages` | ✅ Task 4 已完成 | 归属校验；不存在/不属于本人统一 404 |
| 3 | `client_message_id → assistant reply` 回放 | ✅ Task 3 已完成 | 重试时先查助手回复；有则回放，无则重跑 |
| 4 | 助手消息 meta | ✅ 已加（Task 2） | 已补 `actions`、`signals{amount,pending_question,refund_eligible}` |
| 5 | `reply_to_client_message_id` | ✅ 已加（Task 1） | 独立列 + 唯一部分索引；迁移脚本已加（`scripts/migrate_ch08.py`）；回放见 #3 |
| 6 | 保存顺序 | ✅ Task 2 已改 | 先同事务保存 assistant + flow，再展示；失败 fail-closed |
| 7 | 进度帧 | ✅ Task 5 已完成 | `subgraphs=True` + 语义 stage；spike 见 Task 0 |
| 8 | 转人工入口 | ✅ Task 6 已完成 | 结构化入口，不跑图、不建单、清空流程 |

---

## P1 · 产品功能缺口

| # | 项 | 来源 | 触发/优先级 |
|---|---|---|---|
| 1 | D 出口 `create_ticket`（确认 + `request_id` 幂等） | `00-tasks.md` P4-3 | 中；需要工单闭环时 |
| 2 | 多意图澄清（“您想先处理哪一件”） | P5-11 | 中；双诉求输入占比上升时 |
| 3 | ReAct 槽位解析（“第一单”“那个推车”接回流程） | P5-14 | 中；slot_fill 失败率高时 |
| 4 | B 出口参数失败分支接 ReAct | P5-10（可选） | 低；参数抽取失败/澄清 >20% 时 |
| 5 | 发票申请 / 补发漏发 | 意图设计 §十一 | 低；业务确认需要时 |
| 6 | 真实退款提交 / 工单 / 坐席队列 | Ch08 §11 明确不做 | 后置；写操作需确认/幂等/权限/审计 |
| 7 | 登录鉴权 / 多用户 | Ch08 §11 | 后置；当前 `demo_user_id` |

---

## P2 · 评测与测量边界

| # | 项 | 来源 | 说明 |
|---|---|---|---|
| 1 | 定作 + 已发货未签收的业务口径 | B07 / H06 / 规则表冲突 | 当前按 `reject_then_refund`；真实业务更可能是人工/扣定制费，需业务确认后统一规则表、gold、prompt |
| 2 | 退款流水线件级字段 | T04/T05、H08/H10 | `condition_ok` / `packaging_opened` 只有订单级；要拆到件级或让 compliance 按件判断 |
| 3 | held-out 非严格盲测 | Ch07 验收 | 运行后修正过 T01/T03 gold；后续改 prompt 必须换新样本 |
| 4 | B5–B7 relevance 不适用 | judge-variance | 安全兜底被通用 judge 判低；未实现独立 `safe_handling` 指标 |
| 5 | judge 方差 / `judge_samples=1` | judge-variance | 关键结论看确定性指标；必要时改 3 次采样 |
| 6 | 简单组 G04 边界误判 | refund-hard | 6–7 天边界，模型判超期；低优先级 |
| 7 | 评测集扩到 100+ | Ch07 设计限制 | 当前 44 条 / 57 chunk；扩样本放到验收后 |

---

## P3 · 技术债与清理

| # | 项 | 位置 | 说明 |
|---|---|---|---|
| 1 | 过时 TODO：dense 待验证 | `app/core/retrieval.py:257` | Ch07 已用 35 条稀有词复核，结论维持 dense；应删或改注释 |
| 2 | 无 ALTER 迁移 | `scripts/init_db.py` | ✅ Ch08 Task 1：`scripts/migrate_ch08.py` 已加；README 已补迁移命令（Task 12） |
| 3 | 历史 run-02 快照 `retrieval=hybrid` | EvalFlow 历史 | 已修代码，不回写历史 run |
| 4 | `.env` / DB 不进仓库 | 仓库根 | 已遵守；提交前检查 |
| 5 | 单进程会话锁 | `app/core/lock.py` | 多 worker 要换 Redis/advisory lock |
| 6 | Chroma / Redis / Ollama 依赖 | `local-env.md` | 新环境按文档启动 |
| 7 | schema 比对只查单向索引 | `scripts/init_db.py › diff` | ✅ Ch08 Task 1：已补 `orm_idx - sql_idx`，双向检查 |
| 8 | C2-lite 的 `ack` 丢失边界 | Ch08 spec §7.4 | `ack` 丢失时前端没有 `conversation_id`，重试会新建会话，原会话可能成为孤儿；单会话 UI 不展示，跨会话去重不在 Ch08 范围 |

---

## P4 · 触发后再做的优化

来源：`电商客服-项目进度与交接.md` §九 + 各设计文档“后续优化”

| # | 项 | 触发条件 |
|---|---|---|
| 1 | 查询改写 + 同义词扩展 | 知识类 hit@3 < 0.8 |
| 2 | 多 query 扩写 + 去重 | 退款政策 hit@3 偏低 |
| 3 | 法规 chunk 白话摘要 | 法规类 hit@3 < 0.8 |
| 4 | 检索三段式（扩写/去重/head-tail） | 同上 |
| 5 | 结果复核节点 `review` | 能写出 ≥3 条可执行规则 |
| 6 | 三层渲染层 2（工具结果半压） | 对话 >20 轮或工具返回 >500 字 |
| 7 | 意图扩到 13+ 后两级分类 | 意图数 >12 |
| 8 | 数据飞轮完整闭环（标准化/审核/FAQ 写回/评测） | 落池条目 >50，或要做飞轮演示；见 [Ch09 plan](../superpowers/plans/ch09-flywheel.md) |
| 9 | RAG 缓存 | 重复查询多或延迟成瓶颈；key 含 kb_version+user_id+top_k |
| 10 | 向量库对账 / 幂等键归档 / 冷存储 | FAQ >200 / >10 万行 / 保留 >1 年 |
| 11 | 分布式限流 / Last-Event-ID / 优先级队列 | 多实例 / 断流 >5% / 重要用户等不到 |
| 12 | 手机号/地址脱敏、字段分级 | 接入真实用户数据 |
| 13 | Playwright 端到端 | 手工四场景不够时 |

---

## P5 · 交付清单（P9）

| # | 项 | 状态 |
|---|---|---|
| 1 | README 定稿（≤150 行，含局限） | ✅（124 行） |
| 2 | 三张截图（主界面 / 退款进度 / 知识问答引用） | ✅（`docs/assets/ch08/`） |
| 3 | 2 分钟演示视频（四个场景） | ⏳ |
| 4 | 仓库公开前检查（无密钥、无真实数据） | ⏳ |

---

## 来源索引

| 类型 | 文件 |
|---|---|
| 任务状态 | `docs/superpowers/plans/00-tasks.md` |
| 跨文档优化清单 | `docs/design/电商客服-项目进度与交接.md` §九 |
| Ch08 范围与开放问题 | `docs/superpowers/specs/ch08-frontend-design.md` §11/§14 |
| 评测已知边界 | `docs/reports/ch07-acceptance.md`、`refund-hard.md`、`judge-variance.md` |
| 各章不做/限制 | `docs/superpowers/specs/chNN-*.md`、`docs/design/*.md` 的“待定/后续优化” |
| 过时代码 TODO | `app/core/retrieval.py:257` |
