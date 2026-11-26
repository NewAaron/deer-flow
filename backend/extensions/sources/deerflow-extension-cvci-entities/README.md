# CVCI entity pickers for DeerFlow

This independently installed extension gives the four CVCI welcome cards an archive search and single-selection panel. Selecting a record and pressing **查询** submits a natural-language question through DeerFlow's native composer, together with an `extension_mentions` reference. It does not fetch related records, filter approvals by validity, change prompts or model defaults, or replace the Agent's existing MCP query flow.

Requires `deerflow-extension-api >= 0.2.10` and a host supporting the optional `composer-picker` surface and `SurfaceContext.confirmSelection`. The extension is enabled by default when installed; operators can set `enabled: false` when loading it. The same deployment-owned switch controls browser/action availability and reference middleware registration. Restart after changing deployment configuration.

## Install

From the DeerFlow checkout, use the existing trusted extension installation workflow:

```sh
make extension-install SOURCE=/absolute/path/to/deerflow/examples/deerflow-extension-cvci-entities
```

Restart the Gateway after installing or upgrading extensions. The deployment must already have the `cvci` MCP server and the existing CVCI browser SSO/session grant configured. This package has no credentials, private configuration, database, migrations, or direct CVCI HTTP requests.

## Search and confirmation

An explicit versioned CVCI MCP `ACCESS_DENIED` result remains a permission denial
(`mcp_read_access_denied`), with host-owned safe guidance to contact the CVCI
administrator. It is never shown as an empty result or an invalid search. Other
malformed or failed upstream results retain their existing error handling.

The `cvci-entities.v1` browser module declares four `composer-picker` surfaces:

| Surface ID | Search modes |
| --- | --- |
| `vehicle-record` | Vehicle license plate |
| `permit-query` | Site name, vehicle plate, or approval name/number |
| `project-info` | Site/project name |
| `device-record` | Device number, vehicle plate, or site/disposal site/checkpoint name |

The panel debounces Chinese and English input for 250 ms, waits for IME composition to finish, and requests 12 candidates per page. **加载更多** appends candidates, deduplicates by provider and ID, and preserves the current selection. A failed next page preserves loaded results and retries that page. A new search, tab or type change clears selection and results. Search errors are shown separately from zero results. Pending responses are ignored after another search, a mode change, or disposal. Candidates are rendered as text and support keyboard single selection. CSS uses the host's theme variables within its Shadow DOM, including dark mode and narrow screens.

The browser calls only the `search_subjects` action. That action declares the exact read binding `McpReadTool("cvci", "cvci_search_archives")` and calls the host-provided, action-lifetime `context.mcp_reader`. It requires an authenticated browser session; no service identity or user-ID grant lookup fallback is provided. It accepts only `kind`, `keyword`, `keywordMode`, `pageNo`, and `pageSize: 12`. Vehicle searches omit `keywordMode` so CVCI's existing keyword collection search supports partial plates; its `IDENTIFIER` mode would require an exact code. Other subjects use name or identifier mode as selected. It sends no status, category, validity, or relation filters. Approvals retain their actual `TRANSPORT` or `DISPOSAL` category from the result.

Confirmation uses the native host contract:

```js
await context.confirmSelection({
  reference: { namespace: "cvci-entities", provider: "site", id: "canonical-uuid", label: "display name" },
  text: "帮我查询这个工地关联的核准。",
});
```

Providers are `vehicle`, `site`, `device`, `disposal`, `checkpoint`, `permit-transport`, and `permit-disposal`. IDs are archive UUIDs; names and display codes are not identifiers. Labels are for display only. The reference does not grant permission or attest to facts. The original CVCI tools remain responsible for tenant, access, existence, relation, and current-state checks.

## Turn-scoped reference projection

The lead-agent middleware explicitly opts into `MiddlewarePlacement.model_message_projection`. It appends valid references from the latest human message to that message's temporary model request as JSON identifiers. The host validates this append-only text projection and preserves the original request's model, tools, system context, runtime, message IDs and metadata. The extension omits labels, preserves multimodal blocks, and does not write model-facing text back to history. An ordinary subsequent question does not reuse an older selection. Unknown providers, malformed IDs, or more than 16 references belonging to this extension raise the host's fixed `ModelInputRejected()` re-selection error; references belonging to other extensions are left alone. It adds no tools or forced calls, and does not inject references into subagents or system operations.

The first release asks for current installation relations. It does not introduce an installation-history ledger or infer associations from similar names. Device archive numbers and access codes use the existing tool's identifier search; upstream external-source fingerprints are not selectable archive IDs.

## Checks

With this package and the public API on `PYTHONPATH` in an environment with the declared dependencies:

```sh
python -m pytest -q tests
node --test tests/test_browser.mjs
node --check deerflow_extension_cvci_entities/static/index.mjs
```

Python tests cover exact read bindings, session-only admission, search payload restrictions, pagination, approval categories, malformed upstream responses, and non-persisted turn projection. Browser contract tests cover the four surfaces, search modes, canonical references, and category consistency. Native host/browser regression tests additionally exercise draft preservation, attachments, confirmation, cancellation, focus restoration, and narrow/dark layouts.
