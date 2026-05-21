# Development

Development 文档汇总 Getting Started 之外的维护资料，包括概念词表、runtime 配置、reference、架构取舍和公开 API。

## 维护入口

| 入口 | 什么时候读 |
| --- | --- |
| {doc}`concepts/index` | 对齐 task module、workspace model、task contract 和 glossary。 |
| {doc}`runtime/index` | 维护 `perago start`、worker process、Conductor poll/result、LakeFS workspace publication。 |
| {doc}`reference/index` | 核对 input/output contract、TaskDef 字段、环境变量、失败分类和 troubleshooting。 |
| {doc}`architecture/index` | 理解 transaction model、publication fence 和 ADR 背后的取舍。 |
| {doc}`api/index` | 查看公开 Python API、类型签名和 docstring。 |

(development-getting-started)=
## Getting Started 深入

- {doc}`getting-started/index`：任务声明总入口，包含最小 workspace task、核心命令、生成的 TaskDef 和 controls。
- {doc}`getting-started/workspace-task`：workspace task 的签名、字段边界和约束。
- {doc}`getting-started/workspace-free-task`：workspace-free task 的 contract 和限制。
- {doc}`getting-started/pydantic-contracts`：Pydantic params/result contract 的精确规则。
- {doc}`getting-started/guardrails`：pre/post guardrail 的写法和约束。
- {doc}`getting-started/controls-and-taskdef`：controls 到 TaskDef 字段的映射。
- {doc}`getting-started/examples`：可运行示例和反例索引。

(development-runtime)=
## Runtime 深入

- {doc}`runtime/index`：runtime 总入口。
- {doc}`runtime/configuration`：`.env`、进程环境变量和本机目录配置。
- {doc}`runtime/cli`：`perago check`、`extract`、`start` 的 CLI 行为。
- {doc}`runtime/conductor`：Conductor poll/result、attempt fence 和 worker 运行边界。
- {doc}`runtime/lakefs`：LakeFS workspace 下载、同步和 cleanup。
- {doc}`runtime/workspace-publication`：workspace publication 生命周期。
- {doc}`runtime/publish-budget`：publish budget 与 `responseTimeoutSeconds` 的关系。
- {doc}`runtime/logging`：worker 日志目录和日志字段。
- {doc}`runtime/worker-processes`：supervisor、broker、executor 和 execution mode。

(development-reference)=
## Reference 深入

- {doc}`reference/index`：reference 总入口。
- {doc}`reference/input-output-contract`：Conductor input/output contract。
- {doc}`reference/conductor-taskdef`：TaskDef 字段参考。
- {doc}`reference/environment-variables`：环境变量参考。
- {doc}`reference/failure-classification`：失败分类和结果状态。
- {doc}`reference/troubleshooting`：故障排查入口。

(development-architecture)=
## Architecture 深入

- {doc}`architecture/index`：架构总入口。
- {doc}`architecture/transaction-model`：事务模型和 fail-closed 边界。
- {doc}`architecture/publish-fences`：publish fence 的保证和限制。

(development-api)=
## API 深入

- {doc}`api/index`：公开 API 总入口。
- `task`、`models`、`runtime`、`workspace`、`staging`、`config`、`results`、`errors` 分类页都从这里进入。

```{toctree}
:hidden:
:maxdepth: 2

concepts/index
runtime/index
reference/index
architecture/index
api/index
```
