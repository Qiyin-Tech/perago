# Perago

[![codecov](https://codecov.io/gh/Qiyin-Tech/perago/graph/badge.svg?token=TO0V8X49OF)](https://codecov.io/gh/Qiyin-Tech/perago) 

[文档站（Read the Docs）](https://perago.readthedocs.io) · [PyPI](https://pypi.org/project/perago/)

Perago 是一个面向 Conductor worker 的 typed Python 运行时层，用来在版本化 workspace 上执行任务。它把任务函数签名、Pydantic 输入输出契约、Conductor TaskDef、LakeFS workspace 下载与按需发布、guardrail 校验和 worker 启动边界收敛到同一套模型里。

本文档提供项目概览、安装方式和最小入口；完整说明见文档站。

## 安装

要求 Python 3.10 或更新版本。

```bash
uv add perago
```

或：

```bash
pip install perago
```

仓库本地开发使用：

```bash
uv sync
```

## 最小示例

下面是一个最小的 workspace task。任务作者只需要声明 typed params/output、workspace 约束和任务元数据；Perago 负责校验、TaskDef 提取和运行时集成。

```python
from pathlib import Path

from pydantic import BaseModel, Field

from perago import WorkspaceSpec, require_glob, task


class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)


class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/",
        pre=[require_glob("raw/**/*.parquet", min_count=1)],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

workspace-free task 使用同一套 decorator 和 typed contract，但不声明 `workspace: Path`，也不声明 `WorkspaceSpec`。

## 快速入口

`perago` CLI 的 MVP 入口是单任务 module target：

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 4
```

- `perago check`：导入模块并校验任务声明与本地 runtime config。
- `perago extract`：生成带嵌入 schema 的 Conductor TaskDef JSON。
- `perago start`：在真实 Conductor 配置下启动 supervisor-managed worker processes；workspace task 还需要 LakeFS 配置。

## 任务失败信息

任务返回值表示成功业务结果。执行失败通过原生异常 API 声明：

```python
from perago import TaskFailed, TaskTerminalError

# 相同输入可安全重试，状态为 FAILED。
raise TaskFailed("service unavailable", output_data={"error": {"code": "SERVICE_UNAVAILABLE"}})

# 相同输入不应自动重跑，状态为 FAILED_WITH_TERMINAL_ERROR。
raise TaskTerminalError("result unknown", output_data={"error": {"code": "RESULT_UNKNOWN", "providerTaskId": "original-job"}})
```

| 参数 | 类型 | 必填与默认 | 语义 |
| --- | --- | --- | --- |
| `reason` | str | 必填 | 写入 reasonForIncompletion，受 PERAGO_FAILURE_REASON_MAX_LENGTH 限制，默认 500 字符。 |
| `output_data` | dict[str, JSON value] 或 None | 仅关键字参数，默认 None | 可选失败信息，直接写入 outputData；None 不提供输出，空对象保留为空对象。 |

失败信息支持 string、boolean、integer、有限 number、null、list 和字符串键的 object；不接受 Python 对象、tuple、非字符串键、NaN/Infinity 或循环容器。异常构造时复制数据，调用方随后修改原对象不会改变上报内容。Perago 不解释业务 code、不推断重试许可，也不将失败信息包装成成功 `result`。

原生 `perago start` 的线程/进程模式会在同一次 SDK 更新中发送失败状态、reason 和 outputData；无需自定义 runner。失败信息不按成功返回值 model 校验，不进入成功 TaskDef output schema。失败仍不触发 workspace publication，工作流是否继续由 Conductor 的 retry/optional 等配置决定。调用方负责脱敏；原因截断不截断 output_data。

## 文档

- 文档首页：https://perago.readthedocs.io/zh-cn/latest/
- Getting Started：https://perago.readthedocs.io/zh-cn/latest/getting-started/
- LakeFS 发布协议：https://perago.readthedocs.io/zh-cn/latest/lakefs-publication-protocol.html
- Development：https://perago.readthedocs.io/zh-cn/latest/development.html

仓库内的对应内容可从这里继续展开：

- [文档首页](docs/index.md)
- [Getting Started](docs/getting-started/index.md)
- [LakeFS 发布协议](docs/lakefs-publication-protocol.md)
- [Development](docs/development.md)
