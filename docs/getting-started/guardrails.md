# Guardrails

Guardrail 是 workspace task 的本地文件检查。它检查 Perago 为当前 attempt 准备好的本地 workspace root，确认输入文件已经存在，或确认业务函数产出了承诺的输出文件。

Guardrail 只看 `WorkspaceSpec(prefix=...)` 暴露给业务函数的本地目录树。Pydantic 数据校验、Conductor TaskDef schema、跨 repository 的 LakeFS 查询都不走 guardrail。

## 最小示例

```python
from pathlib import Path

from pydantic import BaseModel

from perago import (
    WorkspaceSpec,
    forbid_glob,
    require_dir,
    require_file,
    require_glob,
    task,
)


class BuildFeaturesParams(BaseModel):
    feature_set: str


class BuildFeaturesOutput(BaseModel):
    row_count: int
    feature_count: int


@task(
    name="features.build",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(
        prefix="/audio/render",
        pre=[
            require_dir("raw"),
            require_file("manifest.json"),
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
    output_dir = workspace / "features"
    output_dir.mkdir(exist_ok=True)
    (output_dir / f"{params.feature_set}.parquet").write_text("ok", encoding="utf-8")
    return BuildFeaturesOutput(row_count=100, feature_count=24)
```

Required/optional/generated 字段边界：

- required: `WorkspaceSpec(pre=...)` 和 `WorkspaceSpec(post=...)` 中的每个 guardrail 都必须使用 workspace-relative path 或 glob pattern。
- optional: `pre` 和 `post` 都可省略；省略表示该阶段没有文件检查。
- generated: guardrail 不生成 Conductor input/output 字段，不写入 TaskDef JSON Schema，也不改变业务函数签名。
- forbidden: task 作者不能 import 或构造内部 guardrail model；只使用 `require_file`、`require_dir`、`require_glob` 和 `forbid_glob`。

## 支持的检查

`require_file(path)` 要求 `workspace / path` 是文件。它适合声明 manifest、单个输入文件或必须产出的标记文件。

`require_dir(path)` 要求 `workspace / path` 是目录。它适合声明输入目录或输出目录存在。

`require_glob(pattern, min_count=1, max_count=None)` 统计 `workspace.glob(pattern)` 的匹配数量。默认至少需要 1 个匹配；`max_count` 为 `None` 时不限制上限。

`forbid_glob(pattern)` 要求 `workspace.glob(pattern)` 没有任何匹配。它适合禁止临时文件、调试文件或不应发布的中间产物。

`require_file` 和 `require_dir` 不接受 count bound。`require_glob` 的 `min_count` 和 `max_count` 必须满足 `min_count <= max_count`。

## Path 规则

Guardrail path 是 workspace 逻辑路径，按 `WorkspaceSpec(prefix=...)` 暴露出的 workspace root 解析。字符串形式必须使用 `/` 分隔，并且必须留在 workspace root 内。

合法写法：

```python
from pathlib import Path, PureWindowsPath

require_file("manifest.json")
require_file(Path("raw") / "manifest.json")
require_glob(Path("raw") / "**" / "*.parquet", min_count=1)
require_file(PureWindowsPath("raw") / "windows-authored.json")
```

这些输入会规范化为 POSIX workspace path：

```text
manifest.json
raw/manifest.json
raw/**/*.parquet
raw/windows-authored.json
```

非法写法会在 module import、`perago check` 或 `WorkspaceSpec(...)` 构造时失败：

```python
require_file("/raw/manifest.json")     # 不能以 / 开头
require_file("../raw/manifest.json")   # 不能逃出 workspace root
require_file("raw/../manifest.json")   # 不能包含 .. segment
require_file(r"raw\manifest.json")     # 字符串不能使用反斜杠
```

如果任务声明 `WorkspaceSpec(prefix="audio/render")`，那么 `require_file("manifest.json")` 检查的是 LakeFS ref 中的 `audio/render/manifest.json`。guardrail 不会检查 repository 根目录下的 `manifest.json`。

## 执行顺序

Workspace task attempt 的 guardrail 顺序固定：

1. Perago 下载 Conductor input 中的 workspace ref。
2. Perago 将 `WorkspaceSpec(prefix=...)` 映射到本地 attempt workspace。
3. `pre` guardrails 检查本地 workspace root。
4. pre 检查通过后，Perago 调用业务函数。
5. 业务函数返回值通过 output Pydantic model 校验。
6. `post` guardrails 检查同一个本地 workspace root。
7. post 检查通过后，Perago 才会 stage、发布 workspace output 并报告成功结果。

Pre guardrail 失败表示上游输入 workspace 不满足当前 task 的输入文件契约。Perago 不调用业务函数，不发布 workspace output，并把异常映射为 terminal failure：

```json
{
  "status": "FAILED_WITH_TERMINAL_ERROR",
  "reasonForIncompletion": "pre guardrail require_glob('raw/**/*.parquet') matched 0 files; min_count=1"
}
```

Post guardrail 失败表示业务函数返回成功，但没有产出承诺的 workspace 文件。Perago 不上传或发布这个 attempt 的 workspace，并把异常映射为普通失败，由 Conductor retry 策略决定是否重试：

```json
{
  "status": "FAILED",
  "reasonForIncompletion": "post guardrail require_glob('features/**/*.parquet') matched 0 files; min_count=1"
}
```

## 与 TaskDef 的关系

Guardrail 属于 Perago runtime metadata。生成的 TaskDef 仍只描述 `workspace`、`params` 和 `result`。

这意味着：

- `pre` 和 `post` 不会出现在 `inputKeys`、`outputKeys`、`inputSchema` 或 `outputSchema` 中。
- 修改 guardrail 会改变 Perago runtime 校验行为，但不会给 Conductor 增加新的输入字段。
- `perago check` 可以在本地提前发现非法 path、非法 count bound 和非法 workspace 声明。

## 常见拒绝场景

```python
# require_file 和 require_dir 不接受数量边界。
require_file("manifest.json", min_count=1)


# min_count 不能大于 max_count。
require_glob("raw/**/*.parquet", min_count=10, max_count=1)


# workspace-free task 不能声明 WorkspaceSpec，因此也不能声明 guardrail。
@task(
    name="features.summarize",
    owner_email="data@example.com",
    workspace=WorkspaceSpec(pre=[require_file("manifest.json")]),
)
def summarize(params: BuildFeaturesParams) -> BuildFeaturesOutput:
    ...
```

完整正例可参考仓库测试夹具 `tests/fixtures/app/workers/features_build.py`。
