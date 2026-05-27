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
- `perago start`：在真实 Conductor 和 LakeFS 配置下启动 supervisor-managed worker processes。

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
