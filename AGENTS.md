# Repository Guidelines

## Project Structure & Module Organization
- `src/freecad_mcp/`: Core MCP server code. `server.py` is the packaged entrypoint and `server_fast_mcp.py` hosts tools built on the standalone FastMCP runtime. Keep reusable helpers in this package.
- `addon/FreeCADMCP/`: FreeCAD workbench files that must be copied into the FreeCAD `Mod/` directory during manual installs.
- `assets/` and `examples/`: Reference media and conversation samples. Update these only when new workflows are worth documenting.
- Root files: `pyproject.toml` defines the Python package, `uv.lock` pins dependencies for `uv`. Add new scripts or dependencies there.

## Build, Test, and Development Commands
- `uv run freecad-mcp [--only-text-feedback]`: Launch the MCP server against a running FreeCAD RPC instance.
- `uv run python -m compileall src/freecad_mcp`: Quick syntax check before submitting code.
- `uv run python -m pytest`: Reserved for the automated test suite; add tests under `tests/` and use this command locally and in CI once available.

## Coding Style & Naming Conventions
- Python 3.12+, 4-space indentation, and comprehensive type hints (see `server_fast_mcp.py`).
- Prefer descriptive, snake_case function and variable names; class names use PascalCase.
- Logging should go through the module-level `logger` to keep FreeCAD RPC issues diagnosable.
- Keep helper utilities near their consumers unless they are shared across modules.

## Testing Guidelines
- Target `pytest` for new coverage. Name files `tests/test_<feature>.py` and mirror the package layout when possible.
- Exercise FreeCAD XML-RPC boundaries with mocks; only hit a live FreeCAD instance in integration-style tests guarded by markers (e.g., `@pytest.mark.integration`).
- Ensure new tools include at least a smoke test that validates success and failure payload formatting.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, present-tense summaries (`update mcp`, `Add basic ...`). Include context when touching multiple areas.
- Reference related issues in the PR description, list manual verification steps (e.g., `uv run freecad-mcp`), and attach screenshots or GIFs when UX changes are visible in FreeCAD.
- Keep PRs focused; split refactors and feature work when possible to simplify review.

## Security & Configuration Tips
- Use the `FREECAD_MCP_DEBUGPY_*` environment variables before running `uv run freecad-mcp` if you need to attach a debugger.
- Avoid committing credentialed `claude_desktop_config.json` files; share example snippets via README or the `/examples` folder instead.
