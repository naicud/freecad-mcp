[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/neka-nat-freecad-mcp-badge.png)](https://mseep.ai/app/neka-nat-freecad-mcp)

# FreeCAD MCP

This repository is a FreeCAD MCP that allows you to control FreeCAD from Claude Desktop.

It now ships with both a classic stdio transport and a FastAPI-powered Server-Sent
Events (SSE) service. Choose the option that best matches how your MCP client prefers
to connect.

## MCP server commands

Once installed with [`uvx`](https://docs.astral.sh/uv/guides/tools/), the project
provides two executables:

| Command           | Transport                              | Purpose                                                                      |
| ----------------- | -------------------------------------- | ---------------------------------------------------------------------------- |
| `freecad-mcp`     | stdio                                  | Launches the original MCP server that Claude Desktop starts as a subprocess. |
| `freecad-mcp-sse` | Server-Sent Events (FastAPI + uvicorn) | Hosts the same tools over HTTP with configurable host/port/SSE paths.        |

The SSE server accepts the same `--only-text-feedback` flag as the stdio version and
adds options for `--host`, `--port`, `--sse-path`, and `--message-path` so it can be
embedded into existing FastAPI deployments or reverse proxies.

## Demo

### Design a flange

![demo](./assets/freecad_mcp4.gif)

### Design a toy car

![demo](./assets/make_toycar4.gif)

### Design a part from 2D drawing

#### Input 2D drawing

![input](./assets/b9-1.png)

#### Demo

![demo](./assets/from_2ddrawing.gif)

This is the conversation history.
https://claude.ai/share/7b48fd60-68ba-46fb-bb21-2fbb17399b48

## Install addon

FreeCAD Addon directory is

- Windows: `%APPDATA%\FreeCAD\Mod\`
- Mac: `~/Library/Application\ Support/FreeCAD/Mod/`
- Linux:
  - Ubuntu: `~/.FreeCAD/Mod/` or `~/snap/freecad/common/Mod/` (if you install FreeCAD from snap)
  - Debian: `~/.local/share/FreeCAD/Mod`

Please put `addon/FreeCADMCP` directory to the addon directory.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
cd freecad-mcp
cp -r addon/FreeCADMCP ~/.FreeCAD/Mod/
```

When you install addon, you need to restart FreeCAD.
You can select "MCP Addon" from Workbench list and use it.

![workbench_list](./assets/workbench_list.png)

And you can start RPC server by "Start RPC Server" command in "FreeCAD MCP" toolbar.

![start_rpc_server](./assets/start_rpc_server.png)

## Setting up Claude Desktop

Pre-installation of the [uvx](https://docs.astral.sh/uv/guides/tools/) is required.
Edit the Claude Desktop config file, `claude_desktop_config.json`, and choose the
transport that fits your workflow.

### stdio transport (default)

Use this option when Claude Desktop should launch the MCP server as a subprocess.

#### User configuration

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": ["freecad-mcp"]
    }
  }
}
```

To save tokens, append the `--only-text-feedback` flag so only text responses are
returned:

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": ["freecad-mcp", "--only-text-feedback"]
    }
  }
}
```

#### Developer configuration

Clone this repository and point Claude Desktop at the local checkout so you can edit
the code without reinstalling the package:

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
```

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": ["--directory", "/path/to/freecad-mcp/", "run", "freecad-mcp"]
    }
  }
}
```

### SSE transport (FastAPI + uvicorn)

Run the SSE server when you need to expose FreeCAD MCP over HTTP, share it between
multiple clients, or take advantage of streaming responses provided by FastMCP.

Start the server in a terminal:

```bash
uvx freecad-mcp-sse --host 127.0.0.1 --port 8099
```

Adjust `--host`, `--port`, `--sse-path`, `--message-path`, or `--only-text-feedback`
to match your environment. Pass `--help` to list every option.

Then register the SSE endpoint with Claude Desktop (requires Claude Desktop 0.6.1 or
later):

```json
{
  "mcpServers": {
    "freecad-sse": {
      "type": "sse",
      "url": "http://127.0.0.1:8099/sse"
    }
  }
}
```

Claude discovers the message endpoint from the SSE handshake automatically. If you
change `--sse-path` or `--message-path`, update the URL above to match the new SSE
path.

## Tools

- `create_document`: Create a new document in FreeCAD.
- `create_object`: Create a new object in FreeCAD.
- `edit_object`: Edit an object in FreeCAD.
- `delete_object`: Delete an object in FreeCAD.
- `execute_code`: Execute arbitrary Python code in FreeCAD.
- `insert_part_from_library`: Insert a part from the [parts library](https://github.com/FreeCAD/FreeCAD-library).
- `get_view`: Get a screenshot of the active view.
- `get_objects`: Get all objects in a document.
- `get_object`: Get an object in a document.
- `get_parts_list`: Get the list of parts in the [parts library](https://github.com/FreeCAD/FreeCAD-library).

## Contributors

<a href="https://github.com/neka-nat/freecad-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=neka-nat/freecad-mcp" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
