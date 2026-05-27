# Commands

Perago task module 的日常入口是三个命令：`check`、`extract` 和 `start`。它们都接收 Python import path，目标 module 必须只声明一个 Perago task。

## 最小命令流

```bash
perago check app.workers.features_build
perago extract app.workers.features_build --output generated/features.build.json
perago start app.workers.features_build -j 2
```

`perago check` 会导入 module、校验 task contract、加载 runtime config，并确认 TaskDef 可以生成。它不连接 Conductor 或 LakeFS。

`perago extract` 使用同一套校验，把 generated Conductor TaskDef 写到指定 `.json` 文件。它不会注册 TaskDef。

`perago start` 是长运行 worker 入口。启动前需要 `CONDUCTOR_SERVER_URL`、LakeFS endpoint、LakeFS access key、LakeFS secret key 已配置，并且 Conductor 中已经注册同名 TaskDef。

## 本仓库 fixture

本仓库 fixture 示例在 `tests/fixtures` 下。本地验证 fixture 时用：

```bash
PYTHONPATH=tests/fixtures uv run perago check app.workers.features_build
PYTHONPATH=tests/fixtures uv run perago extract app.workers.features_build --output /tmp/features.build.json
PYTHONPATH=tests/fixtures uv run perago check app.workers.metadata_validate
PYTHONPATH=tests/fixtures uv run perago extract app.workers.metadata_validate --output /tmp/metadata.validate.json
```

`features_build` 是 workspace task，`metadata_validate` 是 workspace-free task。两者都适合作为 task module 结构参考；完整代码见 {doc}`examples`。

## 目标参数

命令目标是 import path，不是文件路径：

```bash
perago check app.workers.features_build
```

不要写成：

```bash
perago check app/workers/features_build.py
```

`perago check`、`perago extract` 和 `perago start` 会复用同一套 module import 和 task discovery 规则。一个 module 没有 task、定义多个 task、函数签名不合法或 Pydantic contract 不合法时，都会在这些入口暴露为诊断错误。

## 继续阅读

- CLI 的完整运行时行为见 {doc}`../runtime/cli`。
- 环境变量和 `.env` 规则见 {doc}`../runtime/configuration`。
- Conductor poll/result 和 worker 运行边界见 {doc}`../runtime/conductor`。
