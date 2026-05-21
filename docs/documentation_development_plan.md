# Perago 文档开发计划（Read the Docs / NumPy 级别）

> 适用对象：后续负责补全文档的 agent，以及维护 Perago 的内部 Conductor 节点开发者。  
> 检查范围：`CONTEXT.md`、`README.md`、`docs/`、`src/perago/`、`tests/`、`pyproject.toml`。  
> 核心约束：不拆分“用户文档”和“企业文档”；但仍按开发任务组织为指南、参考、架构、运维和故障排查。

---

## 1. 总目标

把 Perago 建成一个可在 Read the Docs 托管的、接近 NumPy 文档质量的内部库文档站点。这里的“NumPy 级别”不指体量，而指以下质量标准：

1. **每个公开 API 都有稳定入口**：从 API 总览能进入每个导出类、函数、异常和数据结构。
2. **每个公开 API 都有结构化 docstring**：使用 numpydoc 风格，包含 `Parameters`、`Returns`、`Raises`、`See Also`、`Notes`、`Examples` 等必要部分。
3. **叙述文档和 API 参考相互闭环**：开发者先从任务开发流程理解 Perago，再在 API 参考里查精确签名和边界条件。
4. **示例可复制、可运行、可回归**：示例优先来自 `tests/fixtures` 和 `tests/`，避免与源码漂移。
5. **构建可复现**：Read the Docs 配置、文档依赖、Sphinx 配置、质量门禁全部进入仓库。
6. **内部受众优先**：文档可以假定读者理解 Conductor、LakeFS、Python/Pydantic，但不能假定读者已经理解 Perago 的术语和事务边界。

---

## 2. 非目标

1. 不在本轮文档工作中改变 Perago 运行时语义。
2. 不新增“外部营销站点”或“产品说明书”。
3. 不把 `docs/transaction_model/` 中的 Seata 背景材料原样放到主导航里；它只能作为架构背景或归档材料。
4. 不把未导出的私有实现当作公共 API 文档化，例如 `_WorkspaceGuardrail`、`_canonical_workspace_path`、`_task_attr` 等。
5. 不在文档里记录真实 Conductor、LakeFS 凭证、内部仓库地址、生产 URL 或真实业务数据。

---

## 3. 当前仓库事实判断

### 3.1 已有内容

当前仓库已经有足够的“事实材料”，但还没有可托管的文档站点：

- `CONTEXT.md`：术语表和关系模型，是概念文档的主要来源。
- `docs/mvp_examples.md`：包含任务定义、CLI、运行配置、工作区、guardrail、Conductor 输入输出、事务模型、TaskDef 生成等大量内容，但目前过长，不适合作为单页主文档。
- `docs/adr/`：已有 ADR，可保留为架构决策档案。
- `docs/conductor/task_def.md`：Conductor TaskDef 字段说明，可转成 Perago TaskDef 参考页。
- `src/perago/__init__.py`：通过 `__all__` 导出 65 个对象，应作为公开 API 文档的初始边界。
- `tests/`：覆盖了任务 API、TaskDef、执行、工作区、metadata、LakeFS runtime、Conductor runtime、配置、CLI、supervisor 等行为，应作为示例和边界条件的事实来源。

### 3.2 主要文档债务

1. **没有 Read the Docs 配置**：仓库根目录缺少 `.readthedocs.yaml`。
2. **没有 Sphinx 站点配置**：`docs/` 缺少 `conf.py`、`index.md` 或 `index.rst`、API 自动生成入口。
3. **绝大多数公开 API 缺少 docstring**：除 CLI 命令和异常类外，大多数导出对象没有结构化 docstring。
4. **现有文档存在阶段漂移风险**：`README.md` 中仍有“`perago start` 尚未接入外部服务”的表述，但源码已经包含 Conductor/LakeFS runtime 和 supervisor 启动路径。后续 agent 必须以源码和测试为最终事实来源，不能直接复制旧文案。
5. **`docs/mvp_examples.md` 信息密度过高**：它适合作为内容来源，不适合作为最终站点单页。
6. **API 分层未明确**：`__all__` 同时导出了 task author API、运行时 API、workspace sync helper、metadata helper、结果对象、配置对象和错误类型。文档必须明确哪些是“日常任务开发 API”，哪些是“高级运行时/集成 API”。

---

## 4. 文档语言策略

推荐采用“双层语言策略”：

1. **叙述文档使用中文**：包括 Getting Started、任务开发、运行配置、事务模型、故障排查。
2. **源码 docstring 建议使用英文 numpydoc 风格**：这样更容易通过 numpydoc 的语法和风格校验，也便于 Python `help()`、Sphinx API 页面和未来外部依赖交叉引用。
3. **API 页面可补中文解释段**：在自动生成 API reference 之外，为每个 API 分类页写中文导读，解释常见用法和选择边界。
4. 如果团队强制要求 docstring 也使用中文，则保留 numpydoc 的章节结构，但只启用“参数、返回、异常是否完整”的校验，不启用英文语法类检查。

---

## 5. 技术方案

### 5.1 文档工具链

采用：

- Sphinx：站点生成。
- numpydoc：解析 NumPy 风格 docstring。
- sphinx.ext.autodoc：从源码 docstring 生成 API 文档。
- sphinx.ext.autosummary：生成 API summary 和每个对象的 stub 页面。
- MyST Parser：保留 Markdown 写作方式，减少现有 `.md` 文档迁移成本。
- pydata-sphinx-theme：接近 NumPy/PyData 风格的导航、搜索、浅色/深色主题。
- sphinx-copybutton：为代码块增加复制按钮。
- 可选：`sphinxcontrib.autodoc_pydantic`，用于更清晰展示 Pydantic model 字段、约束和 schema。Perago 的公开模型大量继承 `pydantic.BaseModel`，因此建议启用。

### 5.2 需要新增的根目录文件

```text
.readthedocs.yaml
```

建议内容：

```yaml
version: 2

build:
  os: ubuntu-24.04
  tools:
    python: "3.10"

sphinx:
  configuration: docs/conf.py
  fail_on_warning: true

python:
  install:
    - requirements: docs/requirements.txt
    - method: pip
      path: .
```

说明：

- Perago 的 `pyproject.toml` 已要求 Python `>=3.10`，Read the Docs 构建也固定到 Python 3.10。
- `fail_on_warning: true` 应在文档基本稳定后打开。如果初始迁移警告过多，可以先关闭，待 M3 阶段再打开。
- 如果内部 Read the Docs 实例不支持 `ubuntu-24.04`，退回其支持的最新 Ubuntu 镜像。

### 5.3 需要新增的文档依赖文件

```text
docs/requirements.txt
```

建议初始内容：

```text
sphinx>=8
numpydoc>=1.10
pydata-sphinx-theme>=0.18
myst-parser
sphinx-copybutton
sphinx-design
autodoc-pydantic>=2
```

首次构建通过后，应把这些依赖 pin 到明确版本，保证 Read the Docs 构建可复现。

### 5.4 需要新增的 Sphinx 配置

```text
docs/conf.py
```

建议骨架：

```python
from __future__ import annotations

project = "Perago"
author = "Perago maintainers"
release = "0.1.0"

extensions = [
    "myst_parser",
    "numpydoc",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.autodoc_pydantic",
]

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"

html_theme = "pydata_sphinx_theme"
html_title = "Perago"

myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

numpydoc_show_class_members = False
numpydoc_show_inherited_class_members = False

# 第一阶段只启用结构完整性检查，避免中文叙述和英文风格检查冲突。
numpydoc_validation_checks = {
    "GL08",  # object does not have a docstring
    "PR01",  # parameters missing
    "PR02",  # unknown parameters
    "PR03",  # parameter order mismatch
    "PR04",  # parameter missing type
    "PR07",  # parameter missing description
    "RT01",  # missing Returns
    "RT03",  # return value missing description
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}
```

如后续 agent 的本地容器不能联网安装 Conductor/LakeFS SDK，可临时在 `conf.py` 中加入：

```python
autodoc_mock_imports = [
    "conductor",
    "conductor.client",
    "lakefs",
    "lakefs_sdk",
]
```

但 Read the Docs 正式构建应优先安装真实依赖，而不是 mock；否则 API 类型和 import 失败可能被隐藏。

---

## 6. 目标文档目录结构

建议把当前 `docs/` 重构为以下结构：

```text
docs/
  conf.py
  index.md
  getting-started/
    index.md
    workspace-task.md
    workspace-free-task.md
    pydantic-contracts.md
    guardrails.md
    controls-and-taskdef.md
    examples.md

  concepts/
    index.md
    glossary.md
    task-module.md
    workspace-model.md
    task-contract.md

  runtime/
    index.md
    configuration.md
    cli.md
    worker-processes.md
    logging.md
    conductor.md
    lakefs.md
    workspace-publication.md
    publish-budget.md

  reference/
    index.md
    conductor-taskdef.md
    input-output-contract.md
    environment-variables.md
    failure-classification.md
    troubleshooting.md

  architecture/
    index.md
    transaction-model.md
    publish-fences.md
    adr/
      index.md
      0001-use-tcc-inspired-workspace-transaction.md
      0002-classify-workspace-guardrail-failures-by-phase.md

  api/
    index.rst
    task.rst
    models.rst
    runtime.rst
    workspace.rst
    staging.rst
    config.rst
    results.rst
    errors.rst
    generated/

  _static/
    custom.css
```

### 6.1 首页 `docs/index.md`

首页必须回答 5 个问题：

1. Perago 是什么？  
   一个内部任务运行时上下文，用于 typed Python workers 在版本化 workspace 上执行 Conductor tasks。
2. 它解决什么问题？  
   任务契约、Conductor TaskDef、LakeFS workspace 输入输出、attempt-local workspace、发布事务和 guardrail 的统一边界。
3. 谁应该读？  
   内部 Conductor 节点开发者、任务作者、运行时维护者。
4. 最小任务长什么样？  
   给出 workspace task 和 workspace-free task 两段最小代码。
5. 下一步读什么？  
   新任务作者进入 `getting-started/`；运维/集成开发者进入 `runtime/`；查 API 进入 `api/`；查设计取舍进入 `architecture/`。

---

## 7. 公开 API 文档边界

以 `perago.__all__` 为初始公开 API 边界。后续 agent 需要为以下对象创建 API 页面并补 docstring。

### 7.1 任务作者日常 API（最高优先级）

| 类别 | API |
|---|---|
| 任务声明 | `task`, `TaskDefinition`, `load_module_task` |
| Workspace 声明 | `WorkspaceSpec` |
| Guardrail | `require_file`, `require_dir`, `require_glob`, `forbid_glob`, `check_guardrails` |
| 任务控制 | `TaskControls`, `RetryPolicy`, `TimeoutPolicy`, `ExecutionLimits`, `PublishBudget` |
| 契约模型 | `WorkspaceInput`, `WorkspaceOutput` |
| TaskDef | `build_taskdef`, `write_taskdef` |
| 错误 | `TaskDefinitionError`, `TaskInputError`, `RuntimeConfigError`, `GuardrailViolation`, `PreGuardrailViolation`, `PostGuardrailViolation` |

这些对象必须先完成 docstring，因为它们直接影响任务开发者能否上手。

### 7.2 运行时和集成 API（第二优先级）

| 类别 | API |
|---|---|
| 执行 | `run_workspace_task_attempt`, `run_workspace_free_task_attempt`, `invoke_workspace_task_body`, `invoke_workspace_free_task`, `build_workspace_task_output`, `build_workspace_free_task_output`, `StagedWorkspace` |
| Runtime result | `RuntimeTaskResult`, `completed_result`, `failed_result`, `terminal_failed_result`, `result_for_exception` |
| 配置 | `ConductorConfig`, `LakeFSConfig`, `RuntimeConfig`, `load_runtime_config` |
| Worker runtime | `WorkerRuntime`, `prepare_worker_runtime`, `WorkerChildSpec`, `worker_child_specs`, `restart_backoff_seconds` |
| Attempt fence | `assert_current_attempt_snapshot`, `StaleAttemptError` |
| 发布 fence | `PublishFenceError` |

这些对象应标记为“高级运行时/集成 API”，避免任务作者误以为必须直接调用。

### 7.3 Workspace 同步和 staging API（第三优先级）

| 类别 | API |
|---|---|
| Workspace 文件同步 | `WorkspaceUploadFile`, `WorkspaceDownloadFile`, `WorkspaceSyncPlan`, `build_workspace_sync_plan`, `workspace_upload_files`, `workspace_download_files`, `workspace_delete_object_paths`, `workspace_local_path` |
| Staging branch | `staging_branch_name` |

这些对象多数用于运行时维护和测试。Perago 不再公开 LakeFS commit metadata publication helper。

---

## 8. API reference 页面生成方案

`docs/api/index.rst` 建议：

```rst
API Reference
=============

.. toctree::
   :maxdepth: 2

   task
   models
   runtime
   workspace
   staging
   config
   results
   errors
```

`docs/api/task.rst` 示例：

```rst
Task API
========

.. currentmodule:: perago

.. autosummary::
   :toctree: generated/

   task
   TaskDefinition
   load_module_task
   WorkspaceSpec
   require_file
   require_dir
   require_glob
   forbid_glob
   check_guardrails
```

每个分类页都要有一段中文导读，说明这一组 API 的使用者、常见入口和不该直接使用的情况。

---

## 9. Docstring 编写标准

### 9.1 总规则

1. 每个 `perago.__all__` 对象必须有 docstring。
2. 每个函数 docstring 至少包含：短摘要、参数、返回值、异常、示例。
3. 每个类 docstring 至少包含：用途、字段/属性、不可变性、验证规则、示例。
4. 每个 Pydantic model 需要说明：字段语义、默认值、校验规则、是否 `extra="forbid"`、是否 frozen。
5. 每个错误类型需要说明：触发条件、运行时结果映射、是否 retryable。
6. 示例必须最小、可复制，优先来自 tests 或 fixtures。
7. 不能在 docstring 里承诺源码没有实现的行为，例如严格 exactly-once publication。
8. 如果 docstring 描述会改变生成的 JSON Schema，不要通过 `Field(description=...)` 实现，除非维护者明确接受 TaskDef schema 输出变化。

### 9.2 函数 docstring 模板

```python
def require_glob(pattern: str | PathLike[str], *, min_count: int = 1, max_count: int | None = None) -> _WorkspaceGuardrail:
    """Require files matching a workspace-relative glob pattern.

    Parameters
    ----------
    pattern : str or os.PathLike[str]
        Workspace-relative POSIX glob pattern. Absolute paths, ``..``
        segments, drive-qualified paths, and backslash-separated strings are
        rejected.
    min_count : int, default=1
        Minimum number of matches required for the guardrail to pass.
    max_count : int or None, default=None
        Maximum number of matches allowed. ``None`` disables the upper bound.

    Returns
    -------
    _WorkspaceGuardrail
        Internal guardrail object consumed by ``WorkspaceSpec``.

    Raises
    ------
    TaskDefinitionError
        If the pattern is not a valid workspace-relative path or if the count
        bounds are inconsistent.

    See Also
    --------
    require_file : Require one file.
    require_dir : Require one directory.
    forbid_glob : Reject files matching a glob pattern.

    Examples
    --------
    >>> WorkspaceSpec(
    ...     prefix="/",
    ...     pre=[require_glob("raw/**/*.parquet", min_count=1)],
    ... )
    WorkspaceSpec(...)
    """
```

如果 `_WorkspaceGuardrail` 不希望暴露在最终 API 中，`Returns` 可写成 `Workspace guardrail`，不要鼓励用户 import 私有类型。

### 9.3 类 docstring 模板

```python
class PublishBudget(BaseModel):
    """Operational time budget for workspace publication.

    ``PublishBudget`` derives the Conductor response timeout used by the
    generated TaskDef and constrains LakeFS merge and Conductor completion
    calls at runtime. It is a timing assumption, not a distributed transaction
    proof.

    Parameters
    ----------
    observed_merge_p99_seconds : int
        Observed high-percentile LakeFS merge latency under expected workload.
    safety_margin_seconds : int
        Additional margin added to the observed merge latency.
    lakefs_merge_timeout_seconds : int
        Request timeout for the LakeFS merge operation. Must cover the observed
        merge latency plus margin.
    conductor_completion_timeout_seconds : int
        Request timeout for reporting the final task result to Conductor.
    worker_shutdown_grace_seconds : int
        Shutdown grace period reserved after publication.
    heartbeat_interval_seconds : int
        Heartbeat interval included in the response timeout calculation.

    Attributes
    ----------
    response_timeout_seconds : int
        Derived Conductor ``responseTimeoutSeconds`` value.

    Raises
    ------
    TaskDefinitionError
        If the LakeFS merge timeout is smaller than the observed merge latency
        plus safety margin.

    Notes
    -----
    This model is only valid for workspace tasks. Workspace-free tasks reject
    ``TaskControls(publish_budget=...)``.
    """
```

---

## 10. 页面内容开发计划

### 10.1 Concepts

从 `CONTEXT.md` 拆出：

- `concepts/glossary.md`：术语表，保留 “Avoid” 说明。
- `concepts/task-module.md`：Task Module、Task Worker、Task Contract、Task Controls。
- `concepts/workspace-model.md`：Workspace Input、Workspace Output、Workspace Prefix、Attempt Workspace、Workspace Branch、Workspace Ref。
- `concepts/task-contract.md`：为什么函数签名是唯一契约来源，为什么不重复声明 params/output。

验收标准：读者读完 concepts 后，可以解释 workspace task 和 workspace-free task 的输入输出形状。

### 10.2 Getting Started task guide

从 `docs/mvp_examples.md` 拆出：

- `getting-started/workspace-task.md`：workspace task 最小例子、签名规则、Path 注入、workspace prefix。
- `getting-started/workspace-free-task.md`：workspace-free task 最小例子。
- `getting-started/pydantic-contracts.md`：Pydantic params/output 模型、extra field 拒绝、JSON Schema 生成。
- `getting-started/guardrails.md`：`require_file`、`require_dir`、`require_glob`、`forbid_glob`，pre/post 失败分类。
- `getting-started/controls-and-taskdef.md`：`TaskControls` 到 Conductor TaskDef 字段映射，publish budget 如何影响 `responseTimeoutSeconds`。
- `getting-started/examples.md`：从 `tests/fixtures/app/workers/` 选 3 个正例和若干反例。

验收标准：一个新任务作者可以照文档写出 task module，运行 `perago check`，再运行 `perago extract`。

### 10.3 Runtime

新增或拆分：

- `runtime/configuration.md`：`.env`、环境变量优先级、默认 workspace/log root、log size/retention 格式。
- `runtime/cli.md`：`perago check`、`perago extract`、`perago start -j`，包含输入、输出、失败示例。
- `runtime/worker-processes.md`：Worker Supervisor、Worker Process、Worker ID、process count、restart backoff。
- `runtime/logging.md`：JSONL、UTC+08:00、per-worker log 文件路径、rotation/retention。
- `runtime/conductor.md`：poll、attempt snapshot、result update、TaskDef 必须预注册。
- `runtime/lakefs.md`：workspace download、stage、merge、cleanup staging。
- `runtime/workspace-publication.md`：TCC-inspired try/confirm/cancel。
- `runtime/publish-budget.md`：如何用观测值和安全边界配置 publish budget。

验收标准：运行时维护者可以从这些页面判断一次 task attempt 的生命周期和故障分类。

### 10.4 Reference

新增：

- `reference/input-output-contract.md`：Conductor input/output JSON shape。
- `reference/conductor-taskdef.md`：生成 TaskDef 字段、默认值、`None` 字段省略规则。
- `reference/environment-variables.md`：所有环境变量表格。
- `reference/failure-classification.md`：`COMPLETED`、`FAILED`、`FAILED_WITH_TERMINAL_ERROR` 的来源。
- `reference/troubleshooting.md`：常见错误文本、原因、修复方法。

验收标准：报错可以反向定位到文档中的条目。

### 10.5 Architecture

保留并整理：

- `architecture/transaction-model.md`：只讲 Perago 的 workspace transaction；Seata TCC/Saga/XA/AT 只能作为背景，不能作为主模型。
- `architecture/publish-fences.md`：Attempt fence、Publish fence、soft fence、hard fence options。
- `architecture/adr/`：保留 ADR 原文，新增 `index.md` 列表。

验收标准：架构页准确说明 MVP 不是 exactly-once 证明，client-side publish fence 是 soft fence。

---

## 11. 现有文档迁移规则

| 当前文件 | 处理方式 |
|---|---|
| `CONTEXT.md` | 拆成 `concepts/glossary.md` 和多个概念页；根目录可保留，但 README 应指向新页面。 |
| `README.md` | 缩减为项目概览、安装、最小示例、文档链接；删除或修正过时的 integration-phase 表述。 |
| `docs/mvp_examples.md` | 作为内容源拆分；最终不进入主导航，或放入 `architecture/archive/mvp-examples.md`。 |
| `docs/adr/*.md` | 移到或保留在 `docs/architecture/adr/`；加 `index.md`。 |
| `docs/conductor/task_def.md` | 改写成 `reference/conductor-taskdef.md`；字段必须与 `src/perago/taskdef.py` 的 `CONTROL_FIELD_MAP` 对齐。 |
| `docs/transaction_model/tcc.md` | 提炼到 `architecture/transaction-model.md`。 |
| `docs/transaction_model/saga.md`、`xa.md`、`at.md` | 不放主导航；只作为 archived background，除非 Perago 设计页明确引用。 |

---

## 12. 质量门禁

后续 agent 完成每个里程碑后都要运行或准备以下检查。

### 12.1 本地构建

```bash
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

初始迁移期间如 warning 过多，可临时不用 `-W`，但最终必须恢复。

### 12.2 测试套件

```bash
python -m pytest
```

文档工作不应改变运行时语义。若只是新增 docstring 和 docs config，测试应保持通过。

### 12.3 Docstring 校验

至少对 `perago.__all__` 中的对象运行 numpydoc 校验。可以新增脚本：

```text
docs/tools/validate_docstrings.py
```

脚本逻辑：

1. import `perago`。
2. 遍历 `perago.__all__`。
3. 对每个对象调用 numpydoc validate。
4. 忽略已批准的内部/高级 API 例外。
5. 输出缺失 docstring、参数不完整、返回不完整的对象。

### 12.4 API 覆盖检查

新增脚本：

```text
docs/tools/check_api_coverage.py
```

脚本逻辑：

1. 读取 `perago.__all__`。
2. 读取 `docs/api/*.rst` 中的 autosummary 列表。
3. 确认每个导出对象出现在且只出现在一个 API 分类页中。
4. 允许维护者显式标注 deprecated 或 intentionally undocumented，但默认不允许遗漏。

### 12.5 示例一致性

1. 任务开发示例应尽量复用 `tests/fixtures/app/workers/features_build.py` 和 `metadata_validate.py`。
2. JSON 输入输出示例应尽量由实际模型或 `perago extract` 生成，减少手写漂移。
3. 每个错误示例要与测试中的错误文本或源码中的异常文本一致。

### 12.6 外链检查

由于当前要求避免容器内网络访问，`linkcheck` 不作为本地强制门禁。可以在有网络的 CI 或 Read the Docs 构建环境中单独启用。

---

## 13. 分阶段执行计划

### M0：文档构建骨架

任务：

1. 新增 `.readthedocs.yaml`。
2. 新增 `docs/requirements.txt`。
3. 新增 `docs/conf.py`。
4. 新增 `docs/index.md`。
5. 新增 `docs/api/index.rst` 和 API 分类页空壳。
6. 确认 Sphinx 能构建一个最小站点。

验收：

- `python -m sphinx -b html docs docs/_build/html` 能成功。
- 首页、API 首页、至少一个 autosummary 页面可打开。

### M1：信息架构和现有文档拆分

任务：

1. 拆分 `CONTEXT.md`。
2. 拆分 `docs/mvp_examples.md`。
3. 移动或索引 ADR。
4. 重写 `README.md` 为短入口。
5. 标记 `docs/transaction_model/` 中不进入主导航的背景材料。

验收：

- 主导航不再依赖巨型 `mvp_examples.md` 单页。
- 每个核心概念都有稳定 URL。
- README 不含明显过时的 runtime 状态描述。

### M2：任务作者路径

任务：

1. 完成 workspace task 指南。
2. 完成 workspace-free task 指南。
3. 完成 Pydantic contract 指南。
4. 完成 guardrail 指南。
5. 完成 controls 与 TaskDef 指南。
6. 添加 3 个正例和 5 个反例。

验收：

- 新任务作者按文档可以写出合法 task module。
- 文档明确列出不支持的函数形状：错误参数名、展开业务字段、async、默认参数、`*args/**kwargs`、keyword-only、缺少类型注解。

### M3：公开 API docstring 第一轮

任务：

优先补以下对象：

```text
task
TaskDefinition
load_module_task
WorkspaceSpec
WorkspaceInput
WorkspaceOutput
TaskControls
RetryPolicy
TimeoutPolicy
ExecutionLimits
PublishBudget
require_file
require_dir
require_glob
forbid_glob
check_guardrails
build_taskdef
write_taskdef
TaskDefinitionError
TaskInputError
RuntimeConfigError
GuardrailViolation
PreGuardrailViolation
PostGuardrailViolation
```

验收：

- 任务作者日常 API 在 RTD 上都有页面。
- 每个页面都有参数、返回、异常、示例。
- numpydoc 结构校验通过。

### M4：运行时和高级 API docstring

任务：

补齐执行、结果、配置、worker、metadata、workspace sync API 的 docstring。

验收：

- `perago.__all__` 中所有对象都出现在 API reference。
- 高级 API 页面明确说明“任务作者通常不直接调用”。

### M5：运行时、架构和故障排查完善

任务：

1. 完成 runtime 页面。
2. 完成 architecture 页面。
3. 完成 troubleshooting 页面。
4. 从测试和源码收集常见错误文本。

验收：

- 对 pre guardrail、post guardrail、publish fence、stale attempt、runtime config error 的处理有明确说明。
- 对 soft fence 和 hard fence 的边界不夸大。

### M6：Read the Docs 发布和访问控制

任务：

1. 接入 Read the Docs 项目。
2. 确认构建环境安装文档依赖和包依赖。
3. 开启 warning-as-error。
4. 设置版本策略：`latest` 跟 main，`stable` 跟 release/tag。
5. 如果文档包含内部实现细节，使用私有项目或内部托管方式；不要把内部细节公开发布。

验收：

- Read the Docs 构建成功。
- 站点搜索可用。
- 私有/公开访问策略符合内部合规要求。

---

## 14. 后续 agent 的工作规则

1. 先读源码和测试，再改文档。当前 `README.md` 和 `docs/mvp_examples.md` 可能包含阶段性旧表述。
2. 不要为了让文档更好看而改变运行时行为。
3. 若必须改源码，只允许新增 docstring、类型别名说明、轻量注释或不会改变行为的文档辅助配置。
4. 不要把私有 helper 放进 API reference，除非它已经在 `perago.__all__` 中导出。
5. 不要从外部网站复制长段材料；需要背景时只总结为 Perago 语境下的设计说明。
6. 所有示例必须使用假 repository、branch、commit、URL、email 和凭证。
7. 所有 shell 命令都要从仓库根目录可执行。
8. 每新增一个 public API，就要同步更新 `perago.__all__` API 覆盖检查和 API 分类页。
9. 每删除或重命名一个 public API，要在 docs 中增加迁移说明或 deprecated 说明。
10. 对任何“是否 exactly-once”“是否硬事务”“是否企业功能”的表述保持保守，只写当前代码和 ADR 支持的结论。

---

## 15. 最终完成标准

文档项目完成时应满足：

1. Read the Docs 可构建并托管 Perago 文档。
2. Sphinx 构建无 warning。
3. `perago.__all__` 中所有导出对象都有 API 页面。
4. 任务作者日常 API 都有 numpydoc 风格 docstring 和最小示例。
5. `CONTEXT.md` 的术语和关系进入概念页。
6. `docs/mvp_examples.md` 的内容被拆分到可导航页面。
7. `README.md` 只承担入口作用，不再承载完整规范。
8. runtime 文档准确说明 `check`、`extract`、`start`、Conductor、LakeFS、workspace publication、logging 和 worker supervisor。
9. 架构文档准确说明 TCC-inspired transaction、attempt fence、publish fence、soft-fence 风险和 hard-fence 选项。
10. 故障排查文档能覆盖任务定义错误、运行配置错误、guardrail 失败、TaskDef 未注册、workspace 同步失败、发布失败和 cleanup 失败。
11. 文档不泄漏真实内部凭证、真实生产地址或业务数据。

---

## 16. 建议的第一批提交拆分

1. `docs: add sphinx and readthedocs build skeleton`
2. `docs: restructure concepts and getting started guide`
3. `docs: add api autosummary pages`
4. `docs: add numpydoc docstrings for task API`
5. `docs: add runtime and publication guides`
6. `docs: add docstring and api coverage checks`
7. `docs: enable warning-as-error for readthedocs build`
