"""Run jons-mcp-pyright with a workaround for its missing workspaceFolders.

jons-mcp-pyright 0.0.2 initializes the pyright langserver with ``rootUri``
only.  pyright (1.1.4xx) discovers pyrightconfig.json from the LSP
``workspaceFolders`` initialize param, so without it every project setting
(stubPath, typeCheckingMode, exclude, ...) is silently dropped and the
diagnostics tool reports bogus errors.

This wrapper injects the missing param into the initialize request, then
starts the normal MCP server.  Remove it once upstream sends
workspaceFolders itself (see AGENTS.md).
"""

from __future__ import annotations

import sys
from typing import Any

from jons_mcp_pyright import main
from jons_mcp_pyright.lsp_client import PyrightClient

_original_request = PyrightClient.request


async def _request(self: PyrightClient, method: str, params: Any = None) -> Any:
    if method == "initialize":
        data: dict[str, Any] = params
        if "workspaceFolders" not in data:
            root = f"file://{self.project_root.absolute()}"
            params = {
                **data,
                "workspaceFolders": [{"uri": root, "name": self.project_root.name}],
            }
    return await _original_request(self, method, params)


PyrightClient.request = _request


if __name__ == "__main__":
    sys.exit(main())
