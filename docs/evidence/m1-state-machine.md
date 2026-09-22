# M1 证据：Lumen 持久化领域模型与状态机

> 状态：已执行  
> 记录日期：2026-09-21  
> 前置基线：[M0 基线证据](./m0-baseline.md)

## 本阶段实现

- 新增 `poirot/backend/lumen/domain/models.py`：Task、Requirement、LumenRun、Checkpoint、Artifact、TraceEvent 与 `LumenRunStatus`。
- 新增 `poirot/backend/lumen/infrastructure/sqlite_store.py`：SQLite schema、持久化 CRUD、状态转移校验、子 Run 父子关系、Checkpoint 存储和遗留运行中 Run 的协调逻辑。
- 新增 `poirot/backend/tests/lumen/test_sqlite_store.py`：5 条本地状态机测试。
- 本阶段没有改动 `AppRuntime.run_question()`、Poirot 原有 `RunManager`、Agent Graph 或模型调用路径；完成契约与真实恢复控制器仍属于后续阶段。

## 已验证行为

| 用例 | 验证点 |
| --- | --- |
| 持久化重开 | 关闭并重开 SQLite Store 后，Task、Run 和 Checkpoint 可读回 |
| 非法完成 | `queued → completed` 被 `StateTransitionError` 拒绝 |
| 合法完成路径 | `queued → running → candidate_done → verifying → completed` 可完成 |
| 子 Run 谱系 | 父 Run 中断后创建 child Run，父 Run 保持 `interrupted` |
| 进程重启协调 | 仅遗留 `running` Run 被标为 `interrupted/process_restarted` |
| 跨 Task 保护 | Checkpoint 不能引用另一 Task 的 Requirement |

## 测试命令与实际结果

在 `poirot/` 下执行：

```powershell
python -m pytest poirot/backend/tests/lumen/test_sqlite_store.py `
  poirot/backend/tests/v1/unit/runtime/test_run_manager.py `
  poirot/backend/tests/v1/unit/runtime/test_checkpointer.py `
  poirot/backend/tests/v1/unit/runtime/test_run_journal.py `
  poirot/backend/tests/v1/unit/middlewares/test_run_journal_status.py `
  poirot/backend/tests/v1/unit/observability/test_stall_tracker.py
```

实际结果：

```text
collected 39 items
39 passed in 1.45s
```

## 尚未验证或未实现的边界

- SQLite Store 尚未替换 Poirot 原有内存 RunManager；两者目前隔离。
- `completed` 状态机路径存在，是为 M2 的 Completion Verifier 预留；M1 本身尚未执行任何验收命令，因此不能将其视为真实任务完成保障。
- Checkpoint 已持久化元数据，但尚未通过 Controller 创建恢复 Run，也未恢复 Agent/工作区执行环境。
- 未运行任何模型、外部工具、Docker、MCP 或开发任务夹具。
