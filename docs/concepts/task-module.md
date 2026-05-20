# Task Module

Perago 的部署单位是 task module：一个 Python module 中只定义一个 task worker。CLI 和 supervisor 都通过 module target 加载它，例如 `app.workers.features_build`。module target 是 import path，不是文件路径，也不是 `module:app` 对象路径。

## Module ownership

一个 task module 包含 exactly one task worker。这个限制让每个 worker process 的职责清楚：它加载一个 module，poll 一个 Conductor task type，并执行同一个 typed Python function。

这种形状也避免了 app registry 风格的运行时选择器。Perago 不在一个进程里通过 route、handler 或 `--task` 参数挑选多个 task。需要多个 task type 时，应拆成多个 task modules。

## Task worker

task worker 由两部分组成：

- Python 函数：声明业务参数、workspace 注入和返回模型。
- Perago metadata：由 `@task(...)`、`WorkspaceSpec(...)` 和 `TaskControls(...)` 声明 task name、owner、workspace prefix、workspace checks 和运行控制。

`@task(...)` 在 module import 时执行校验。非法签名、非法 workspace prefix、非法 workspace check path、重复 contract 声明或 controls 类型错误都会在 `perago check` 和 worker 启动前暴露出来。

## Worker process

worker process 加载 exactly one task module。`perago start -j` 启动 worker supervisor；supervisor 可以启动多个独立 worker processes，但它不是内部 task scheduler，也不把一个进程变成多 task pool。

多个 worker processes 可以加载同一个 task module，用于提高同一个 Conductor task type 的并发处理能力。每个 process 都有自己的 worker ID，并独立 poll Conductor。

## Supported targets

合法 target 是 Python module import path：

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 4
```

不支持的 target 形状：

- 文件路径，例如 `app/workers/features_build.py`。
- 对象路径，例如 `app.workers.features_build:task`。
- app registry，例如 `app.workers:app`。
- 在同一个文件中定义多个 task worker 再通过参数选择。
