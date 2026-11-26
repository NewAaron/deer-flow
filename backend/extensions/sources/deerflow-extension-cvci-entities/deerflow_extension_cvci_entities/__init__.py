"""CVCI business presentation using public, session-bound extension capabilities."""

from pathlib import Path

from deerflow_extension_api import (
    AgentScope,
    BackendAction,
    BrowserAssets,
    McpReadTool,
    MiddlewarePlacement,
    Placement,
    PluginContribution,
    extension,
)

from .middleware import EntityReferences
from .search import NAMESPACE, search_subjects


class EntityMiddlewareContributor:
    def contribute_middlewares(self, app_store, context):
        if context.scope != AgentScope.LEAD:
            return ()
        return (MiddlewarePlacement(EntityReferences(), Placement.MODEL_LOGICAL, AgentScope.LEAD, model_message_projection=True),)


@extension(api="0.2.10", name="cvci-entities")
def install(registry, config):
    enabled = config.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("CVCI entities requires a boolean enabled setting")
    registered = registry.plugin(
        PluginContribution(
            namespace=NAMESPACE,
            title="CVCI 档案查询 / Archive search",
            description="选择已授权的业务档案，交由原生 Agent 查询。",
            enabled=enabled,
            frontend=BrowserAssets("cvci-entities.v1", Path(__file__).parent),
            backend=(
                BackendAction(
                    "search_subjects",
                    search_subjects,
                    mcp_reads=(McpReadTool("cvci", "cvci_search_archives"),),
                ),
            ),
        )
    )
    if registered is False:
        raise RuntimeError("This host does not support CVCI entity picker contributions")
    if enabled:
        registry.middlewares(EntityMiddlewareContributor())
