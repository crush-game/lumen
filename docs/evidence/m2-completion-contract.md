# M2 证据：Completion Contract 与误完成拦截

> 状态：核心验证服务已执行
> 记录日期：2026-09-22
> 前置证据：[M1 状态机](./m1-state-machine.md)

## 本阶段实现

- 新增 `poirot/backend/lumen/domain/completion.py`：
  `CompletionContract`、`ContractCheck`，支持必需检查、Requirement、Artifact、禁止路径和预算上限。
- 新增 `poirot/backend/lumen/application/completion.py`：
  `CompletionVerifier`、`VerificationEvidence`、`VerificationResult`。
- 新增 `poirot/backend/lumen/application/runtime.py`：
  `LumenTaskSpec`、`LumenRuntimeAdapter`，并在 `AppRuntime` 增加显式的
  `run_development_task()` 入口；该入口要求调用方提供结构化任务规格和独立证据提供器。
- 验证流程固定为：`candidate_done → verifying → completed/contract_failed`。
- 验证器只消费受信的检查结果，不直接执行合同中的任意命令；命令字段是检查定义，实际执行器由后续运行时接入。
- Artifact 必须属于当前 Task/Run 且 `validation.passed` 为真；测试结果必须同时满足 `passed=true` 和 `exit_code=0`。
- 禁止路径、工作区越界、重复检查结果和工具调用预算超限会阻止完成。
- SQLite Store 增加 Requirement、Artifact、TraceEvent 的读取接口，保存 `contract_checked` 审计事件。

## 验收用例

| 用例 | 预期 |
| --- | --- |
| F0：检查通过、Requirement 已覆盖、Artifact 已验证 | `completed` |
| F1：检查失败或 Requirement 未覆盖 | `contract_failed`，不进入 `completed` |
| F1：修改 `tests/` 禁止路径 | `forbidden_path_modified` |
| F1：`passed=true` 但退出码非 0 | `forged_check_result` |
| F1：修改 `../secrets.txt` | `workspace_path_escape` |
| F1：工具调用数超过合同上限 | `budget_exceeded` |

## 测试命令与实际结果

在 `poirot/` 下执行：

```powershell
python -m pytest poirot/backend/tests/lumen `
  poirot/backend/tests/v1/unit/runtime/test_run_manager.py `
  poirot/backend/tests/v1/unit/runtime/test_checkpointer.py `
  poirot/backend/tests/v1/unit/runtime/test_run_journal.py `
  poirot/backend/tests/v1/unit/middlewares/test_run_journal_status.py `
  poirot/backend/tests/v1/unit/observability/test_stall_tracker.py
```

实际结果：

```text
collected 49 items
49 passed in 2.79s
```

## 当前边界

- 本阶段完成的是 Lumen 层的独立完成验证服务，以及显式的 `AppRuntime.run_development_task()` 适配；普通 `run_question()` 路径保持原行为。
- `run_development_task()` 不会从自然语言返回自动猜测 Requirement/Artifact，必须由调用方提供独立证据提供器。
- 因此本证据证明的是“外部完成契约可以拒绝误完成”，不证明模型已经能够完成真实开发任务。
- 任意 Shell 命令的执行、工作区快照和真实测试收集将在后续受控执行器/安全网关中接入。
