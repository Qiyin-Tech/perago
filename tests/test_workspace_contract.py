import pytest
from pydantic import ValidationError

from perago import WorkspaceInput, WorkspaceOutput


WORKSPACE_REF = {
    "repository": "song-000123",
    "branch": "main",
    "ref_type": "commit",
    "ref": "589f87704418c6bac80c5a6fc1b52c245af347b9ad1ea8d06597e4437fae4ca3",
}


def test_workspace_input_and_output_are_distinct_contract_models() -> None:
    workspace_input = WorkspaceInput.model_validate(WORKSPACE_REF)
    workspace_output = WorkspaceOutput.model_validate(WORKSPACE_REF)

    assert type(workspace_input) is WorkspaceInput
    assert type(workspace_output) is WorkspaceOutput
    assert workspace_output.model_dump(mode="json") == WORKSPACE_REF


def test_workspace_contract_models_reject_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceOutput.model_validate({**WORKSPACE_REF, "prefix": "/audio/render"})
