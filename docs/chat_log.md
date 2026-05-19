# Conductor Python SDK 使用

---

## 📤 导出信息
- **会话标题**: Conductor Python SDK 使用
- **导出时间**: 2026/5/19 10:40:58
- **来源**: ChatGPT
- **链接**: https://chatgpt.com/c/6a0a7e56-1d64-8326-b7fb-0559a2d74e74

---

## 🙋 用户

Conductor 的 python sdk 是如何使用的？我们如何构建一种自己使用的包装层，使得我们可以让具体的 worker 代码不知道 Conductor 和 LakeFS 的存在，并且可以自动从绑定代码中抽取出包含 input output schema 的 Task 定义文件？仅内部使用，因此我们可以要求开发者写完整的类型注释。

---

## 🤖 ChatGPT

我会先核对 Conductor Python SDK 的用法和 Task 定义格式，再给出一个内部包装层方案，覆盖开发者 API、绑定层、worker 适配、schema 抽取、LakeFS 隔离和可落地代码骨架。

---

## 🤖 ChatGPT

我会把核心边界定为：业务函数契约、Conductor worker 绑定、Task JSON Schema 定义三者分离；Schema 从内部类型注释生成，运行时只做 payload 与 LakeFS workspace 的翻译。

---

## 🤖 ChatGPT

## 结论

可以做，而且推荐做成 **三层隔离**：



1. **业务 worker 层**：只写普通 Python 函数、Pydantic/dataclass 类型、本地 `Path` 或普通对象；不 import Conductor，不 import LakeFS。
2. **绑定层**：声明这个函数对应哪个 Conductor task、哪些输入来自 JSON、哪些输入/输出是数据 artifact、retry/timeout/domain 等运行策略。
3. **运行时适配层**：负责 Conductor 轮询、Pydantic 校验、LakeFS materialize/commit、异常映射、TaskResult 输出。
4. **定义生成器**：扫描绑定层 registry，生成：
  - Conductor Task Definition JSON；
  - input schema；
  - output schema；
  - 可选 workflow task builder / input wiring helper。


这比直接让业务函数写 `@worker_task` 更干净，因为 Conductor 的设计本身就是 **task definition 和 worker code 分离**：definition 控制 retry、timeout、rate limit 等；worker 只是执行代码。官方也建议生产环境不要依赖 `register_task_def=True`，而是单独管理 task definition。[Orkes+1](https://orkes.io/content/sdks/python)




## 1. Conductor Python SDK 的基本用法

Conductor Python SDK 包名是：



```bash
pip install conductor-python
```

SDK 可从环境变量读取 server URL 和认证信息，例如 `CONDUCTOR_SERVER_URL`、`CONDUCTOR_AUTH_KEY`、`CONDUCTOR_AUTH_SECRET`。[Orkes](https://orkes.io/content/sdks/python)


最简单 worker 写法是：



```python
from conductor.client.worker.worker_task import worker_task

@worker_task(task_definition_name="greet", register_task_def=True)
def greet(name: str) -> str:
    return f"Hello {name}"
```

然后用 `TaskHandler` 启动 worker 轮询：



```python
from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration

config = Configuration()

with TaskHandler(configuration=config, scan_for_annotated_workers=True) as task_handler:
    task_handler.start_processes()
    task_handler.join_processes()
```

`register_task_def=True` 适合本地 quickstart，不适合生产；生产应由 CI/CD 或 metadata 管理流程注册 Task Definition。官方示例也说明 `TaskHandler` 会发现 annotated workers 并启动轮询进程。[GitHub](https://github.com/conductor-oss/python-sdk)


但对你们的包装层，**更建议不用 `@worker_task` 直接装饰业务函数**，而是使用 SDK 的低层 `Worker` 或 `WorkerInterface`。SDK 支持函数式 worker 或 class worker；函数式 worker 可以接收原始 `Task`，也可以接收 `Task.input_data`，输出可以是 `TaskResult` 或普通对象。[GitHub](https://github.com/conductor-oss/python-sdk/blob/main/docs/WORKER.md) 这样基础设施层可以完整控制输入解码、LakeFS 操作、错误处理和输出格式。




## 2. 先分清 Conductor 里的三个东西

容易混淆：



| 概念 | 作用 | 是否可由函数签名自动生成 |
| --- | --- | --- |
| Worker code | 真实执行逻辑 | 可以由绑定层包装 |
| Task Definition | 注册 task type，定义 retry、timeout、rate limit、schema 等 | 可以从绑定层生成 |
| Workflow Task Configuration | 某个 workflow 中如何把 ${workflow.input.x} 或上游输出接到 task 输入 | 只能部分生成，实际 wiring 依赖 workflow 语义 |

Worker task 必须先在 Conductor 里注册 task definition，workflow 才能引用它；workflow task 里的 `inputParameters` 才是实际把数据传给 worker 的地方。[Conductor+1](https://conductor-oss.github.io/conductor/documentation/configuration/taskdef.html)


所以自动生成器应生成 **task catalog / taskdef / schema**，但不应假装能从 worker 函数完全推导 workflow 的业务连线。可以生成 helper：



```python
TrainModelTask(
    task_ref_name="train",
    dataset="${workflow.input.dataset}",
    params="${workflow.input.params}",
)
```

但 workflow 里具体接哪个上游输出，仍应由 workflow 作者声明。




## 3. 推荐的内部开发者体验


### 3.1 业务代码：不知道 Conductor 和 LakeFS


```python
# app/ml/train.py

from pathlib import Path
from pydantic import BaseModel, Field

class TrainParams(BaseModel):
    epochs: int = Field(ge=1, le=200)
    learning_rate: float = Field(gt=0)

class TrainResult(BaseModel):
    metrics: dict[str, float]
    model_dir: Path

def train_model(
    dataset_dir: Path,
    params: TrainParams,
    output_dir: Path,
) -> TrainResult:
    # 这里只处理本地路径和 Python 类型
    # 不知道 Conductor
    # 不知道 LakeFS
    ...
    return TrainResult(
        metrics={"accuracy": 0.91},
        model_dir=output_dir,
    )
```

这里 `dataset_dir` 和 `output_dir` 是本地路径。LakeFS 下载、上传、commit 都由 runtime adapter 做。




### 3.2 绑定代码：声明基础设施语义


```python
# app/ml/bindings.py

from app.ml.train import train_model, TrainParams, TrainResult
from platformkit.binding import task
from platformkit.io import JsonInput, TempDirOutput, LakeFSInputDir, LakeFSOutputDir

train_model_task = task(
    name="ml.train_model",
    description="Train a model from a versioned dataset",
    owner_email="ml-platform@example.com",

    retry_count=2,
    retry_logic="FIXED",
    retry_delay_seconds=60,
    timeout_seconds=3600,
    response_timeout_seconds=300,

    inputs={
        # Conductor 输入 key: dataset
        # Runtime 将它解析成 LakeFS object/prefix，并 materialize 成本地 Path
        "dataset_dir": LakeFSInputDir(task_key="dataset"),

        # Conductor 输入 key: params
        # Runtime 用 Pydantic 解析成 TrainParams
        "params": JsonInput(task_key="params", model=TrainParams),

        # 不来自 Conductor；runtime 创建一个临时目录传给业务函数
        "output_dir": TempDirOutput(),
    },

    outputs={
        # Runtime 把 result.model_dir 上传/提交到 LakeFS，并把引用返回给 Conductor
        "model": LakeFSOutputDir(result_attr="model_dir"),

        # Runtime 把 result.metrics 作为普通 JSON 返回
        "metrics": "metrics",
    },
)(train_model)
```

业务代码不需要任何平台 import。绑定代码知道 LakeFS/Conductor 语义，但绑定代码不执行业务逻辑。




## 4. Conductor 输入输出 schema 怎么生成

Conductor/Orkes 支持 task-level input/output schema，schema 格式是 JSON Schema；开启后，Conductor 会在运行时校验输入输出，不匹配时 task/workflow 会失败。[Orkes](https://orkes.io/content/developer-guides/schema-validation) Task Definition 里可以放 `enforceSchema`、`inputSchema`、`outputSchema`、`inputKeys`、`outputKeys` 等字段。[Orkes](https://orkes.io/content/reference-docs/api/metadata/creating-task-definitions)


内部生成器可以这样做：



```
绑定层 spec
  ├─ task name / retry / timeout / owner
  ├─ input adapters
  │   ├─ JsonInput(model=TrainParams)
  │   └─ LakeFSInputDir(...)
  ├─ output adapters
  │   ├─ LakeFSOutputDir(...)
  │   └─ JsonOutput(...)
  └─ underlying function signature
        train_model(dataset_dir: Path, params: TrainParams, output_dir: Path) -> TrainResult
```

然后生成外部 schema，而不是业务函数的本地 schema。



### 外部 input schema

Conductor 看到的是：



```json
{
  "dataset": {
    "repo": "datasets",
    "ref": "main",
    "path": "training/customer_churn/",
    "type": "directory"
  },
  "params": {
    "epochs": 20,
    "learning_rate": 0.001
  }
}
```

业务函数看到的是：



```python
dataset_dir: Path
params: TrainParams
output_dir: Path
```

这两个世界中间由 adapter 转换。



### 建议定义一个中立的 artifact schema


```python
from pydantic import BaseModel, Field

class ArtifactRef(BaseModel):
    repo: str
    ref: str = Field(description="branch, tag, or commit")
    path: str
    type: str = Field(pattern="^(file|directory)$")
    format: str | None = None
```

这不必叫 LakeFSRef。对业务开发者来说，它只是平台 artifact 引用；runtime 决定它映射到 LakeFS。


Pydantic v2 可以从 `BaseModel.model_json_schema()` 生成 JSON Schema，也可以用 `TypeAdapter.json_schema()` 处理任意类型。[Pydantic](https://pydantic.dev/docs/validation/latest/concepts/json_schema/) 因此你们可以要求内部开发者写完整类型注释，并让生成器 fail closed：缺失 annotation、`Any`、未声明 adapter 的 `Path/DataFrame/file handle` 一律报错。




## 5. 生成出来的文件形态


### 5.1 input schema


```json
{
  "name": "ml.train_model.input",
  "version": 1,
  "type": "JSON",
  "data": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:internal:conductor:task:ml.train_model:input:v1",
    "type": "object",
    "properties": {
      "dataset": {
        "$ref": "#/$defs/ArtifactRef"
      },
      "params": {
        "$ref": "#/$defs/TrainParams"
      }
    },
    "required": ["dataset", "params"],
    "additionalProperties": false,
    "$defs": {
      "ArtifactRef": {
        "type": "object",
        "properties": {
          "repo": {"type": "string"},
          "ref": {"type": "string"},
          "path": {"type": "string"},
          "type": {"type": "string", "pattern": "^(file|directory)$"},
          "format": {"anyOf": [{"type": "string"}, {"type": "null"}]}
        },
        "required": ["repo", "ref", "path", "type"]
      },
      "TrainParams": {
        "type": "object",
        "properties": {
          "epochs": {"type": "integer", "minimum": 1, "maximum": 200},
          "learning_rate": {"type": "number", "exclusiveMinimum": 0}
        },
        "required": ["epochs", "learning_rate"]
      }
    }
  }
}
```


### 5.2 output schema


```json
{
  "name": "ml.train_model.output",
  "version": 1,
  "type": "JSON",
  "data": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:internal:conductor:task:ml.train_model:output:v1",
    "type": "object",
    "properties": {
      "model": {
        "$ref": "#/$defs/ArtifactRef"
      },
      "metrics": {
        "type": "object",
        "additionalProperties": {"type": "number"}
      }
    },
    "required": ["model", "metrics"],
    "additionalProperties": false,
    "$defs": {
      "ArtifactRef": {
        "type": "object",
        "properties": {
          "repo": {"type": "string"},
          "ref": {"type": "string"},
          "path": {"type": "string"},
          "type": {"type": "string", "pattern": "^(file|directory)$"},
          "format": {"anyOf": [{"type": "string"}, {"type": "null"}]}
        },
        "required": ["repo", "ref", "path", "type"]
      }
    }
  }
}
```


### 5.3 task definition


```json
{
  "name": "ml.train_model",
  "description": "Train a model from a versioned dataset",
  "retryCount": 2,
  "retryLogic": "FIXED",
  "retryDelaySeconds": 60,
  "timeoutSeconds": 3600,
  "responseTimeoutSeconds": 300,
  "timeoutPolicy": "TIME_OUT_WF",
  "inputKeys": ["dataset", "params"],
  "outputKeys": ["model", "metrics"],
  "enforceSchema": true,
  "inputSchema": {
    "name": "ml.train_model.input",
    "version": 1,
    "type": "JSON"
  },
  "outputSchema": {
    "name": "ml.train_model.output",
    "version": 1,
    "type": "JSON"
  },
  "ownerEmail": "ml-platform@example.com"
}
```



## 6. Runtime adapter 的核心结构

包装层 runtime 可以用 SDK 的低层 `Worker`，不要把 `@worker_task` 放到业务函数上：



```python
# platformkit/runtime.py

from conductor.client.http.models import Task, TaskResult
from conductor.client.http.models.task_result_status import TaskResultStatus

def make_execute_function(spec):
    def execute(task: Task) -> TaskResult:
        result = TaskResult(
            task_id=task.task_id,
            workflow_instance_id=task.workflow_instance_id,
            worker_id=spec.worker_id,
        )

        try:
            raw_input = task.input_data or {}

            # 1. 用生成的 Pydantic model 再校验一次
            external_input = spec.input_model.model_validate(raw_input)

            # 2. 创建临时目录 / LakeFS transaction
            with spec.io_runtime.open(task=task, external_input=external_input) as io:
                kwargs = {}

                # 3. 将 Conductor JSON 输入转换成业务函数参数
                for param_name, adapter in spec.inputs.items():
                    kwargs[param_name] = adapter.to_python(
                        external_input=external_input,
                        io=io,
                    )

                # 4. 调用真正业务函数
                value = spec.fn(**kwargs)

                # 5. 将业务结果转换成 Conductor outputData
                output_data = {}
                for output_key, adapter in spec.outputs.items():
                    output_data[output_key] = adapter.from_python(
                        value=value,
                        io=io,
                    )

            # 6. 再校验输出
            spec.output_model.model_validate(output_data)

            result.output_data = output_data
            result.status = TaskResultStatus.COMPLETED
            return result

        except Exception as exc:
            result.status = TaskResultStatus.FAILED
            result.reason_for_incompletion = str(exc)
            return result

    return execute
```

启动 worker：



```python
from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration
from conductor.client.worker.worker import Worker

from app.ml.bindings import train_model_task
from platformkit.runtime import make_execute_function

config = Configuration()

workers = [
    Worker(
        task_definition_name=train_model_task.name,
        execute_function=make_execute_function(train_model_task),
        poll_interval=1.0,
        domain=train_model_task.domain,
    )
]

with TaskHandler(workers, config, scan_for_annotated_workers=False) as task_handler:
    task_handler.start_processes()
    task_handler.join_processes()
```

SDK 文档里的 worker runner 也是这种模式：构造 `Worker(task_definition_name=..., execute_function=...)`，再交给 `TaskHandler` 启动。[GitHub](https://github.com/conductor-oss/python-sdk/blob/main/docs/WORKER.md)




## 7. LakeFS 应该隐藏在哪里

LakeFS 高层 SDK 支持 branch、object upload/download、commit、merge、transaction 等操作；其 transaction 会创建临时 branch，在成功时原子 merge，失败时回滚并清理。[docs.lakefs.io+1](https://docs.lakefs.io/integrations/python-versioning-branches/) 这正适合包装层。


建议 runtime 每次 task 执行：



```
Conductor task input
  ↓
解析 ArtifactRef(repo, ref, path)
  ↓
LakeFS 下载或挂载到本地临时目录
  ↓
业务函数读取本地 Path
  ↓
业务函数写 output_dir
  ↓
runtime 上传到 LakeFS 临时 branch
  ↓
commit / merge / tag
  ↓
Conductor outputData 只返回 ArtifactRef，不返回大数据
```

LakeFS object 读写可以通过 high-level SDK 的 `branch.object(...).upload(...)` 和 `reader(...)` 完成。[docs.lakefs.io](https://docs.lakefs.io/integrations/python-data-operations/)



### 推荐 output path 规则

必须考虑 Conductor retry。不要让 retry 随机覆盖上一次输出。


建议：



```
outputs/{task_name}/{workflow_id}/{task_ref_name}/attempt-{retry_count}/...
```

或者：



```
outputs/{task_name}/{workflow_id}/{task_id}/...
```

如果希望 task 幂等，则使用 deterministic path，并在 runtime 里检测已有 commit/artifact 是否可复用。




## 8. 定义生成器算法

伪代码：



```python
# platformkit/generate.py

import inspect
import json
from pathlib import Path
from typing import get_type_hints
from pydantic import BaseModel, create_model

def generate_all(registry, out_dir: Path):
    for spec in registry:
        validate_spec(spec)

        input_model = build_external_input_model(spec)
        output_model = build_external_output_model(spec)

        input_schema = wrap_conductor_schema(
            name=f"{spec.name}.input",
            version=spec.schema_version,
            data=input_model.model_json_schema(),
        )

        output_schema = wrap_conductor_schema(
            name=f"{spec.name}.output",
            version=spec.schema_version,
            data=output_model.model_json_schema(),
        )

        task_def = {
            "name": spec.name,
            "description": spec.description,
            "retryCount": spec.retry_count,
            "retryLogic": spec.retry_logic,
            "retryDelaySeconds": spec.retry_delay_seconds,
            "timeoutSeconds": spec.timeout_seconds,
            "responseTimeoutSeconds": spec.response_timeout_seconds,
            "timeoutPolicy": spec.timeout_policy,
            "inputKeys": list(input_model.model_fields.keys()),
            "outputKeys": list(output_model.model_fields.keys()),
            "enforceSchema": True,
            "inputSchema": {
                "name": f"{spec.name}.input",
                "version": spec.schema_version,
                "type": "JSON",
            },
            "outputSchema": {
                "name": f"{spec.name}.output",
                "version": spec.schema_version,
                "type": "JSON",
            },
            "ownerEmail": spec.owner_email,
        }

        write_json(out_dir / "schemas" / f"{spec.name}.input.v{spec.schema_version}.json", input_schema)
        write_json(out_dir / "schemas" / f"{spec.name}.output.v{spec.schema_version}.json", output_schema)
        write_json(out_dir / "tasks" / f"{spec.name}.json", task_def)

def validate_spec(spec):
    sig = inspect.signature(spec.fn)
    hints = get_type_hints(spec.fn, include_extras=True)

    for name, param in sig.parameters.items():
        if name not in hints:
            raise TypeError(f"{spec.fn.__name__}.{name} 缺少类型注释")

    if "return" not in hints:
        raise TypeError(f"{spec.fn.__name__} 缺少 return 类型注释")

    for param_name in sig.parameters:
        if param_name not in spec.inputs:
            raise TypeError(f"{spec.name}: 参数 {param_name} 没有 input adapter")

def write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
```

这里的重点是：**schema 不是直接从业务函数参数机械生成，而是从“业务函数签名 + binding adapter”共同生成**。否则 `Path`、目录、DataFrame、模型文件等本地执行概念无法正确映射成 Conductor JSON 输入输出。




## 9. 内部类型规则

建议你们制定硬规则：



```
允许：
- pydantic.BaseModel
- dataclass，但最好统一转成 Pydantic model
- str/int/float/bool
- list[T], dict[str, T]
- Literal / Enum
- datetime/date，明确序列化为 ISO string
- Path，但必须绑定到 Artifact adapter 或 TempDir adapter

禁止：
- Any
- Callable
- open file handle
- pandas.DataFrame 直接作为 Conductor input/output
- bytes 大对象直接放 outputData
- 未声明 codec 的自定义类
```

对数据类任务，建议：



```python
def task(input: SomeInput) -> SomeOutput:
    ...
```

而不是很多散乱参数。散乱参数可以支持，但长期维护上不如单 input model 稳定。




## 10. schema 版本与 task 版本策略

Conductor Task Definition 的 `name` 是唯一 task type 名称；schema 本身可以 version。为避免 workflow 执行中 schema 变更造成旧 workflow 失败，建议：



### 非 breaking change

例如新增 optional 字段：



```
task name: ml.train_model
schema version: +1
workflow 可逐步迁移
```


### breaking change

例如字段改名、类型改变、输出语义改变：



```
task name: ml.train_model.v2
schema version: 1
```

不要只改同名 task 的 schema，然后期望所有历史 workflow 都安全。




## 11. 错误处理建议

Conductor 的 retry 由 Task Definition 控制；包装层要把错误分成两类：



| 错误类型 | Runtime 行为 | Conductor status |
| --- | --- | --- |
| 输入 schema 错误 | 不应重试 | FAILED_WITH_TERMINAL_ERROR，如果 SDK 暴露该 status |
| LakeFS 临时网络错误 | 可重试 | FAILED |
| 业务异常 | 默认可重试，但可由业务抛 NonRetryableError | FAILED 或 terminal |
| 输出 schema 错误 | 不应重试，说明代码/绑定错误 | terminal |
| commit conflict | 看策略：可重试或 terminal | 取决于 output path 是否幂等 |

需要给业务侧一个中立异常：



```python
class NonRetryableTaskError(Exception):
    pass
```

业务函数可以抛它，但仍不需要知道 Conductor。




## 12. 最小可行实现顺序

按这个顺序做：



1. **只支持 Pydantic BaseModel + primitive**。
2. 实现 `@task(...)` registry。
3. 实现 schema/taskdef generator。
4. 实现低层 `Worker(..., execute_function=...)` runtime。
5. 加 `LakeFSInputFile/LakeFSInputDir/LakeFSOutputFile/LakeFSOutputDir` adapter。
6. 加 CI 校验：
  - 所有绑定都能生成 schema；
  - 无 `Any`；
  - generated JSON committed；
  - taskdef diff 需要 review。
7. 最后再支持 async、streaming、大文件优化、workflow builder。




## 推荐设计一句话

**业务函数只表达“我需要什么本地输入，产出什么 Python 对象”；绑定层表达“这些输入输出如何映射到 Conductor JSON 和 LakeFS artifact”；生成器把绑定层变成 Task Definition + JSON Schema；runtime 把 Conductor/LakeFS 世界转换成本地函数调用。**

---

## 🙋 用户

帮我填充好

---

## 🤖 ChatGPT

A typed task runtime for executing Conductor workers over versioned workspaces.

---

## 🙋 用户

生成一个初始 README。 还有这个仓库自己的 python 环境用 uv 没问题吧

---

## 🤖 ChatGPT

可以，用 `uv` 没问题，而且这个仓库很适合用 `uv`：



```
perago 本身是 Python package + CLI
需要 pyproject.toml
需要可重复的本地开发环境
需要 CI 里 uv build / uv publish
```

建议：



```
提交 pyproject.toml
提交 uv.lock
使用 src/ layout
包名、CLI 名、repo 名统一叫 perago
```

下面是一个初始 `README.md`。



```markdown
# perago

A typed task runtime for executing Conductor workers over versioned workspaces.

`perago` provides a small internal runtime layer for writing workflow workers as ordinary typed Python functions. It hides workflow orchestration, workspace lifecycle management, schema validation, file guardrails, and commit handling behind a consistent task interface.

The goal is to let business worker code focus on local file operations and typed parameters, without directly depending on Conductor or a specific versioned-storage backend.

## Status

Early internal package. APIs are expected to change before `1.0`.

## Design goals

- Keep business workers simple.
- Avoid direct Conductor SDK usage in task implementation code.
- Avoid direct LakeFS usage in task implementation code.
- Treat each task as a typed operation over a local workspace.
- Generate task metadata from Python type annotations and registration bindings.
- Support workspace guardrails such as required files, glob checks, and mutation constraints.
- Keep the workspace backend replaceable.
- Make local development, CI validation, and worker deployment reproducible.

## Core model

A task is written as a normal Python function:

```python
from pathlib import Path
from pydantic import BaseModel, Field

class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)

class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)

def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    # Read and write files under workspace.
    # No Conductor SDK.
    # No LakeFS SDK.
    # No process pool or global concurrency control.
    ...
```

The task is registered with runtime metadata:



```python
from perago import task, WorkspaceSpec, require_dir, require_glob, forbid_glob

@task(
    name="features.build",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
    workspace=WorkspaceSpec(
        pre=[
            require_dir("raw"),
            require_glob("raw/**/*.parquet", min_count=1),
        ],
        post=[
            require_dir("features"),
            require_glob("features/**/*.parquet", min_count=1),
            forbid_glob("**/*.tmp"),
        ],
    ),
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...
```

At runtime, `perago` is responsible for:



- polling and completing Conductor tasks;
- validating task input and output;
- opening the workflow workspace;
- checking pre-task and post-task file guardrails;
- running the business function;
- committing workspace changes through the configured backend;
- returning typed task output to Conductor.



## Package layout


```
perago/
  pyproject.toml
  uv.lock
  README.md
  src/
    perago/
      __init__.py
      api.py
      runner.py
      schema.py
      guardrails.py
      conductor.py
      workspace/
        __init__.py
        base.py
        lakefs.py
        local.py
  tests/
  examples/
```


## Installation

For local development:



```bash
uv sync
```

Run tests:



```bash
uv run pytest
```

Run the CLI:



```bash
uv run perago --help
```

Build the package:



```bash
uv build
```


## Development

This repository uses `uv` for dependency management, packaging, and local development.


Recommended commands:



```bash
uv sync
uv run pytest
uv run ruff check .
uv run pyright
uv build
```


## CLI

Planned CLI commands:



```bash
perago generate
perago validate
perago worker --task features.build
```

Expected responsibilities:



| Command | Purpose |
| --- | --- |
| perago generate | Generate schemas, task definitions, and catalog files |
| perago validate | Validate task bindings, schemas, and workspace specs |
| perago worker | Start a single-flight worker process for one or more task types |


## Generated artifacts

`perago` can generate metadata from task registrations:



```
generated/
  schemas/
    features.build.input.v1.json
    features.build.output.v1.json
  taskdefs/
    features.build.json
  catalog/
    tasks.yaml
```

Depending on the Conductor version, generated schemas may be registered as Conductor schema metadata or used only by `perago` and CI validation.



## Concurrency model

`perago` assumes a single-flight worker model by default:



```
one worker process = one execution slot
one task attempt at a time per process
no internal business-layer concurrency
```

Global concurrency should be controlled by Conductor task definitions and deployment replica counts.


Examples:



```
Conductor concurrentExecLimit = global task-type safety limit
Docker Compose --scale       = local development worker slots
Kubernetes replicas          = production worker slots
systemd template instances   = bare-metal worker slots
```

Business task functions should not create global thread pools or process pools.



## Workspace model

A workflow instance is expected to operate on a versioned workspace.


The business function receives a local `Path`:



```python
def task_fn(workspace: Path, params: Params) -> Output:
    ...
```

The workspace backend is responsible for:



- resolving the workspace for a workflow instance;
- exposing a local directory;
- restoring or cleaning failed attempts;
- committing successful task changes;
- producing a revision identifier.


The initial backend is expected to be LakeFS, but the task implementation should not depend on LakeFS directly.



## File guardrails

`perago` supports lightweight workspace guardrails.


Examples:



```python
WorkspaceSpec(
    pre=[
        require_dir("raw"),
        require_glob("raw/**/*.parquet", min_count=1),
    ],
    post=[
        require_dir("features"),
        forbid_glob("**/*.tmp"),
    ],
)
```

These checks are not intended to prove complete business correctness. They are guardrails for stable, low-level workspace expectations. Complex conditional validation should remain in task-specific code or custom validators.



## Versioning

The package is currently pre-`1.0`.


Expected policy:



| Version range | Meaning |
| --- | --- |
| 0.x | APIs may change |
| 1.x | Runtime API becomes stable |
| patch releases | Bug fixes only after 1.0 |


## License

Internal use only unless explicitly published under an open-source license.



```

`uv.lock` 我建议提交。虽然它是 library package，但这是企业内部 runtime，不是纯公共库；提交 lockfile 能保证 CI、本地开发、发布构建环境一致。消费者项目依赖 `perago` 时仍然按自己的 lockfile 解析，不会被这个仓库的 `uv.lock` 强制影响。
```

---

## 🙋 用户

感觉如果是一个进程起一个实例的话，整体设计上我们可以参考一下 fastapi?

---

## 🤖 ChatGPT

可以，而且这个类比很有价值。

如果你们最终采用：



```
一个进程 = 一个 single-flight worker instance
一个 instance 一次只执行一个 task attempt
并发由进程数 / Pod replicas / Compose scale 控制
```

那整体设计上确实可以参考 FastAPI 的几个核心抽象：



```
FastAPI app       -> Perago app
route decorator   -> task decorator
request body      -> params model
response model    -> output model
dependency        -> workspace / context / backend 注入
middleware        -> guardrail / marker / commit / logging hooks
uvicorn worker    -> perago worker process
OpenAPI schema    -> Conductor task/schema definitions
```

但不要照搬 HTTP 语义。应该参考它的**声明式注册 + 类型驱动 schema + runtime 解耦**。




# 1. 推荐的核心形态

类似 FastAPI：



```python
from pathlib import Path
from pydantic import BaseModel, Field

from perago import Perago, WorkspaceSpec, require_dir, require_glob

app = Perago(
    name="music-workers",
)

class BuildFeaturesParams(BaseModel):
    feature_set: str
    min_rows: int = Field(ge=1)

class BuildFeaturesOutput(BaseModel):
    row_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)

@app.task(
    name="features.build",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
    workspace=WorkspaceSpec(
        pre=[
            require_dir("raw"),
            require_glob("raw/**/*.parquet", min_count=1),
        ],
        post=[
            require_dir("features"),
            require_glob("features/**/*.parquet", min_count=1),
        ],
    ),
    timeout_seconds=1800,
    retry_count=2,
    concurrent_exec_limit=8,
)
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
) -> BuildFeaturesOutput:
    ...
```

启动 worker：



```bash
perago worker app.workers:app --task features.build
```

生成定义：



```bash
perago generate app.workers:app --out generated/
```

本地多进程：



```bash
docker compose up -d --scale worker_features_build=4
```

K8s：



```
Deployment replicas = 4
每个 Pod 跑：
  perago worker app.workers:app --task features.build
```



# 2. FastAPI 里最值得借鉴的东西


## 2.1 `app` 作为注册中心

FastAPI 里：



```python
app = FastAPI()

@app.get("/items")
def read_items():
    ...
```

你们可以是：



```python
app = Perago()

@app.task("features.build")
def build_features(...):
    ...
```

好处是：



```
所有 task 注册集中在 app registry
CLI 可以 import app，然后 generate / validate / run
测试也可以直接拿 app.tasks 检查
```

不要把任务散落成全局 side effect 后找不到。




## 2.2 decorator 只声明，不执行

`@app.task(...)` 不应该立刻连接 Conductor，也不应该立刻初始化 LakeFS。


它只应该注册 metadata：



```
task name
params model
output model
workspace spec
retry/timeout/concurrency
function reference
```

真正运行时由：



```
perago worker module:app
```

加载。


这和 FastAPI 很像：定义 route 时不启动 server，`uvicorn module:app` 才运行。




## 2.3 类型驱动 schema

FastAPI 用 Pydantic 类型生成 OpenAPI schema。


你们用 Pydantic 类型生成：



```
input schema
output schema
task definition
catalog
```

例如：



```python
@app.task(
    name="features.build",
    params=BuildFeaturesParams,
    output=BuildFeaturesOutput,
)
```

生成：



```
generated/
  schemas/features.build.input.v1.json
  schemas/features.build.output.v1.json
  taskdefs/features.build.json
```

这比手写 Conductor TaskDef 稳定得多。




## 2.4 Dependency Injection，但要克制

FastAPI 的 DI 很强，但你们不要一开始复制完整 DI 系统。


只需要支持少量内置注入：



```python
def build_features(
    workspace: Path,
    params: BuildFeaturesParams,
    context: TaskContext,
) -> BuildFeaturesOutput:
    ...
```

其中：



```
workspace: Path         # 当前 workflow workspace
params: Params          # task 输入参数
context: TaskContext    # workflow_id, task_id, attempt, logger, revision 等
```

可以允许用户不写 `context`：



```python
def build_features(workspace: Path, params: Params) -> Output:
    ...
```

但不要一开始做复杂的：



```python
Depends(...)
yield dependency
dependency override tree
scope resolution
```

MVP 不需要。




## 2.5 Lifespan

FastAPI 有 startup/shutdown 生命周期。


你们也应该有：



```python
app = Perago()

@app.on_startup
def init():
    ...

@app.on_shutdown
def close():
    ...
```

或者更 Pythonic：



```python
app = Perago(
    conductor=ConductorConfig(...),
    workspace_backend=LakeFSWorkspaceBackend(...),
)
```

一个 worker process 启动时：



```
初始化 Conductor client
初始化 workspace backend
加载 task registry
进入 poll loop
```

进程退出时：



```
flush logs
释放本地 workspace lock
关闭 client
```

这比在每个 task 函数里初始化 client 干净。




# 3. 不要照搬 FastAPI 的部分


## 3.1 不要照搬 HTTP 路由心智

不要叫：



```
request
response
route
endpoint
status_code
```

更适合叫：



```
TaskContext
TaskInput
TaskOutput
TaskSpec
WorkspaceSession
```

你的系统不是 HTTP server，是 task runtime。




## 3.2 不要一开始做复杂 async

FastAPI 很多地方围绕 async/event loop。


你们这里如果是：



```
一个进程一次跑一个 task
task 多数是文件/音频/ML/批处理
```

那 sync 函数更直接：



```python
def task_fn(...):
    ...
```

可以以后支持：



```python
async def task_fn(...):
    ...
```

但不是第一优先级。




## 3.3 不要做过度插件化

FastAPI 的生态很大，但你们现在要的是内部 runtime。


第一版不要设计成：



```
plugin manager
event bus
complex extension interface
multi-backend orchestration DSL
```

先把核心闭环做稳：



```
注册 -> 校验 -> 生成 -> poll -> 执行 -> guardrail -> commit -> complete
```



# 4. 一个更完整的 API 草案


## 4.1 App 定义


```python
from perago import Perago

app = Perago(
    name="music-workers",
    version="0.1.0",
)
```



## 4.2 Task 注册


```python
@app.task(
    name="audio.render",
    params=RenderParams,
    output=RenderOutput,
    workspace=WorkspaceSpec(
        pre=[
            require_dir("midi"),
            require_glob("midi/*.mid", min_count=1),
        ],
        post=[
            require_dir("audio"),
            require_glob("audio/*.wav", min_count=1),
            forbid_glob("**/*.tmp"),
        ],
    ),
    retry_count=1,
    timeout_seconds=3600,
    concurrent_exec_limit=4,
)
def render_audio(
    workspace: Path,
    params: RenderParams,
    context: TaskContext,
) -> RenderOutput:
    ...
```



## 4.3 CLI


```bash
perago list app.workers:app
```

输出：



```
features.build
audio.render
model.train
```

生成 metadata：



```bash
perago generate app.workers:app --out generated/
```

校验：



```bash
perago validate app.workers:app
```

启动单 task worker：



```bash
perago worker app.workers:app --task audio.render
```

启动多个 task 类型的 worker：



```bash
perago worker app.workers:app --tasks features.build,model.train
```

但重任务最好一个 worker service 只跑一个 task type。




# 5. 和 FastAPI 的对应关系


| FastAPI | Perago |
| --- | --- |
| FastAPI() | Perago() |
| @app.get(...) | @app.task(...) |
| path operation function | task function |
| request body model | params model |
| response model | output model |
| dependency injection | workspace/context 注入 |
| middleware | guardrail / marker / commit hooks |
| OpenAPI schema | Conductor schema/taskdef/catalog |
| uvicorn module:app | perago worker module:app |
| fastapi run | perago worker / perago generate |

这个类比是成立的。




# 6. 对你们最重要的设计点

如果参考 FastAPI，我建议抓住这三个原则。



## 原则 1：声明和运行分离

业务代码：



```python
@app.task(...)
def fn(...):
    ...
```

只是声明。


运行：



```bash
perago worker module:app --task xxx
```

生成：



```bash
perago generate module:app
```

验证：



```bash
perago validate module:app
```

三者不要混在一起。




## 原则 2：app registry 是唯一事实源

不要一边有：



```
Python decorator
```

一边又手写：



```
taskdefs/*.json
schemas/*.json
catalog.yaml
```

然后互相不一致。


应该是：



```
Python app registry
  -> generate schemas
  -> generate taskdefs
  -> generate catalog
```

生成物可以 commit，但不能手改。




## 原则 3：部署层只复制 worker instance

每个 worker instance：



```
加载 app
筛选 task type
single-flight poll
执行一个 task
循环
```

并发不在 Python 里搞大池子，而在部署层表达：



```
Docker Compose --scale
K8s replicas
systemd template instances
Conductor concurrentExecLimit
```

这和 FastAPI 的部署心智也类似：



```
app 不自己 fork worker
uvicorn/gunicorn/K8s/systemd 决定跑几个 worker
```



# 7. 我会这样写 README 里的定位

可以加一段：



```markdown
## Design inspiration

`perago` follows an application-registry model similar to modern Python web frameworks.

A `Perago` app collects typed task declarations. The same app object can then be used to generate metadata, validate task contracts, or run single-flight worker processes.

```python
app = Perago()

@app.task(...)
def build_features(workspace: Path, params: Params) -> Output:
    ...
```

The runtime keeps task implementation code independent from Conductor and the workspace backend, in the same way a web endpoint should not need to know how the server process is managed.



```

---

# 最终判断

可以参考 FastAPI，而且应该参考。

但参考的是：

```text
app object
decorator registry
typed params/output
schema generation
CLI loads module:app
deployment controls process count
lifespan/config/hooks
```

不是参考：



```
HTTP route semantics
full dependency injection complexity
async-first design
插件生态膨胀
```

对你们来说，最好的形态是：



```python
app = Perago()

@app.task(...)
def worker(workspace: Path, params: Params) -> Output:
    ...
```

然后：



```bash
perago generate app.workers:app
perago worker app.workers:app --task features.build
```

这个形态会比散装函数 + 手写绑定文件更稳定，也比复杂内部框架更容易让团队理解。

---
