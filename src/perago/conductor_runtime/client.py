from __future__ import annotations

from conductor.client.configuration.configuration import Configuration
from conductor.client.orkes.orkes_metadata_client import OrkesMetadataClient
from conductor.client.orkes.orkes_task_client import OrkesTaskClient

from perago.config import ConductorConfig

from .models import ConductorTaskAttempt
from .sdk_mapping import conductor_task_to_attempt


class OrkesConductorRuntimeClient:
    def __init__(
        self,
        *,
        task_client: OrkesTaskClient,
        metadata_client: OrkesMetadataClient,
    ) -> None:
        self._task_client = task_client
        self._metadata_client = metadata_client

    @classmethod
    def from_config(
        cls,
        config: ConductorConfig,
    ) -> OrkesConductorRuntimeClient:
        sdk_config = Configuration(server_api_url=config.server_url)
        return cls(task_client=OrkesTaskClient(sdk_config), metadata_client=OrkesMetadataClient(sdk_config))

    def taskdef_exists(self, task_name: str) -> bool:
        try:
            self._metadata_client.get_task_def(task_name)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_not_found(exc):
                return False
            raise
        return True

    def get_task(self, task_id: str) -> ConductorTaskAttempt:
        return conductor_task_to_attempt(self._task_client.get_task(task_id))


def _looks_like_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status == 404:
        return True
    return "404" in str(exc) and "not" in str(exc).lower()
