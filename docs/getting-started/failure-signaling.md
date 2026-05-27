# Failure Signaling

Task return value 表示成功的业务 `Result Output`。如果函数正常返回 `OutputModel(...)`，Perago 会把 attempt 当成 `COMPLETED`，并把返回值写到 Conductor `output.result`。

业务字段例如 `status="REJECTED"` 或 `status="NEEDS_ACTION"` 不会让 Conductor task 失败。它们应该由 WorkflowDef 分支处理。

## 选择机制

| 情况 | 推荐机制 | Conductor status |
| --- | --- | --- |
| 同一 input 稍后重跑可能成功 | `raise TaskFailed("...")` | `FAILED`，按 retry policy 重试 |
| 用户、上游或 workflow 分支可恢复 | `return Output(status="REJECTED" / "NEEDS_ACTION")` | `COMPLETED`，由 workflow 分支处理 |
| 同一 input 自动重试没有意义 | `raise TaskTerminalError("...")` | `FAILED_WITH_TERMINAL_ERROR`，不重试 |

## 示例

```python
from perago import TaskFailed, TaskTerminalError


def call_model(params: GenerateParams) -> GenerateOutput:
    if prompt_is_blocked(params.prompt):
        return GenerateOutput(status="REJECTED", reason_code="PROMPT_POLICY_VIOLATION")

    if temporary_rate_limit():
        raise TaskFailed("model service rate limited this attempt")

    if params.song_id == "missing":
        raise TaskTerminalError("song_id does not exist")

    return GenerateOutput(status="READY")
```

失败 reason 是短字符串诊断。MVP 使用 `PERAGO_FAILURE_REASON_MAX_LENGTH` 限制写入 Conductor `reasonForIncompletion` 的文本长度，完整细节进入 worker JSONL 日志。

## Guardrail 失败

Pre guardrail 失败表示上游输入 workspace 不满足当前 task 的输入文件契约。Perago 不调用业务函数，并把结果映射为 `FAILED_WITH_TERMINAL_ERROR`。

Post guardrail 失败表示业务函数返回成功，但没有产出承诺的 workspace 文件。Perago 不上传或发布这个 attempt 的 workspace，并把结果映射为 `FAILED`，由 Conductor retry 策略决定是否重试。

Guardrail 写法见 {doc}`guardrails`；精确分类表见 {doc}`../reference/failure-classification`。

## 继续阅读

- Conductor input/output 成功和失败载荷见 {doc}`../reference/input-output-contract`。
- 失败分类的架构决策见 {doc}`../architecture/adr/0005-use-exceptions-for-task-execution-failures`。
