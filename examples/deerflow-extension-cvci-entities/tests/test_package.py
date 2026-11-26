import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from deerflow_extension_api import AgentScope, McpReadTool, Placement

from deerflow_extension_cvci_entities import install


class Registry:
    def __init__(self):
        self.plugins = []
        self.contributors = []

    def plugin(self, value):
        self.plugins.append(value)
        return True

    def middlewares(self, value):
        self.contributors.append(value)


def test_default_install_is_enabled_and_contributes_only_search_and_lead_projection():
    registry = Registry()
    install(registry, {})
    (plugin,) = registry.plugins
    assert plugin.enabled is True and plugin.namespace == "cvci-entities"
    assert plugin.tools == ()
    (action,) = plugin.backend
    assert action.name == "search_subjects"
    assert action.mcp_reads == (McpReadTool("cvci", "cvci_search_archives"),)
    (contributor,) = registry.contributors
    (placement,) = contributor.contribute_middlewares(None, SimpleNamespace(scope=AgentScope.LEAD))
    assert placement.placement == Placement.MODEL_LOGICAL and placement.scope == AgentScope.LEAD
    assert placement.model_message_projection is True
    assert contributor.contribute_middlewares(None, SimpleNamespace(scope=AgentScope.SUBAGENT)) == ()
    manifest = json.loads((Path(plugin.frontend.root) / plugin.frontend.manifest).read_text())
    assert manifest["entry"] == "static/index.mjs"
    assert set(manifest["files"]) == {"static/index.mjs", "static/style.css"}
    assert all((Path(plugin.frontend.root) / name).is_file() for name in manifest["files"])


def test_operator_disabled_install_does_not_register_reference_middleware():
    registry = Registry()
    install(registry, {"enabled": False})
    assert registry.plugins[0].enabled is False
    assert not registry.contributors


@pytest.mark.parametrize("value", [None, 1, "false"])
def test_invalid_switch_fails_registration(value):
    registry = Registry()
    with pytest.raises(ValueError):
        install(registry, {"enabled": value})
    assert registry.plugins == [] and registry.contributors == []
