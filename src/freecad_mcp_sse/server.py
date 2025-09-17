import argparse
import html
import json
import logging
import os
import xmlrpc.client
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Literal, TypeVar

import uvicorn
from fastapi import FastAPI
from fastmcp import Context, FastMCP
from fastmcp.server.http import create_sse_app
from mcp.types import ImageContent, TextContent
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FreeCADMCPserver")


_only_text_feedback = False


T = TypeVar("T")

ToolContent = TextContent | ImageContent
ToolResponse = list[ToolContent]
OperationResult = dict[str, Any]
OperationFn = Callable[["FreeCADConnection"], OperationResult]
MessageFn = Callable[[OperationResult], str]
QueryFn = Callable[["FreeCADConnection"], T]
FormatterFn = Callable[[T], str]

_SCREENSHOT_UNAVAILABLE_MESSAGE = (
    "Note: Visual preview is unavailable in the current view type (such as TechDraw or "
    "Spreadsheet). Switch to a 3D view to see visual feedback."
)
_TEXT_ONLY_MESSAGE = "Visual feedback disabled by the --only-text-feedback option."

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8099
DEFAULT_SSE_PATH = "/sse"
DEFAULT_MESSAGE_PATH = "/messages"


class FreeCADConnection:
    def __init__(self, host: str = "localhost", port: int = 8099):
        self.server = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}", allow_none=True
        )

    def ping(self) -> bool:
        return self.server.ping()

    def create_document(self, name: str) -> dict[str, Any]:
        return self.server.create_document(name)

    def create_object(self, doc_name: str, obj_data: dict[str, Any]) -> dict[str, Any]:
        return self.server.create_object(doc_name, obj_data)

    def edit_object(
        self, doc_name: str, obj_name: str, obj_data: dict[str, Any]
    ) -> dict[str, Any]:
        return self.server.edit_object(doc_name, obj_name, obj_data)

    def delete_object(self, doc_name: str, obj_name: str) -> dict[str, Any]:
        return self.server.delete_object(doc_name, obj_name)

    def insert_part_from_library(self, relative_path: str) -> dict[str, Any]:
        return self.server.insert_part_from_library(relative_path)

    def execute_code(self, code: str) -> dict[str, Any]:
        return self.server.execute_code(code)

    def get_active_screenshot(self, view_name: str = "Isometric") -> str | None:
        try:
            # Check if we're in a view that supports screenshots
            result = self.server.execute_code(
                """
import FreeCAD
import FreeCADGui

if FreeCAD.Gui.ActiveDocument and FreeCAD.Gui.ActiveDocument.ActiveView:
    view_type = type(FreeCAD.Gui.ActiveDocument.ActiveView).__name__
    
    # These view types don't support screenshots
    unsupported_views = ['SpreadsheetGui::SheetView', 'DrawingGui::DrawingView', 'TechDrawGui::MDIViewPage']
    
    if view_type in unsupported_views or not hasattr(FreeCAD.Gui.ActiveDocument.ActiveView, 'saveImage'):
        print("Current view does not support screenshots")
        False
    else:
        print(f"Current view supports screenshots: {view_type}")
        True
else:
    print("No active view")
    False
"""
            )

            # If the view doesn't support screenshots, return None
            if not result.get(
                "success", False
            ) or "Current view does not support screenshots" in result.get(
                "message", ""
            ):
                logger.info(
                    "Screenshot unavailable in current view (likely Spreadsheet or TechDraw view)"
                )
                return None

            # Otherwise, try to get the screenshot
            return self.server.get_active_screenshot(view_name)
        except Exception as e:
            # Log the error but return None instead of raising an exception
            logger.error(f"Error getting screenshot: {e}")
            return None

    def get_objects(self, doc_name: str) -> list[dict[str, Any]]:
        return self.server.get_objects(doc_name)

    def get_object(self, doc_name: str, obj_name: str) -> dict[str, Any]:
        return self.server.get_object(doc_name, obj_name)

    def get_parts_list(self) -> list[str]:
        return self.server.get_parts_list()


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    try:
        logger.info("FreeCADMCP server starting up")
        try:
            _ = get_freecad_connection()
            logger.info("Successfully connected to FreeCAD on startup")
        except Exception as e:
            logger.warning(f"Could not connect to FreeCAD on startup: {str(e)}")
            logger.warning(
                "Make sure the FreeCAD addon is running before using FreeCAD resources or tools"
            )
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _freecad_connection
        if _freecad_connection:
            logger.info("Disconnecting from FreeCAD on shutdown")
            _freecad_connection = None
        logger.info("FreeCADMCP server shut down")


mcp = FastMCP(
    "FreeCADMCP",
    instructions="FreeCAD integration through the Model Context Protocol",
    lifespan=server_lifespan,
)


_freecad_connection: FreeCADConnection | None = None


def get_freecad_connection():
    """Get or create a persistent FreeCAD connection"""
    global _freecad_connection
    if _freecad_connection is None:
        _freecad_connection = FreeCADConnection(host="localhost", port=9875)
        if not _freecad_connection.ping():
            logger.error("Failed to ping FreeCAD")
            _freecad_connection = None
            raise Exception(
                "Failed to connect to FreeCAD. Make sure the FreeCAD addon is running."
            )
    return _freecad_connection


# Helper function to safely add screenshot to response
def add_screenshot_if_available(
    response: ToolResponse, screenshot: str | None
) -> ToolResponse:
    """Attach screenshot feedback when possible."""
    if screenshot is not None and not _only_text_feedback:
        response.append(
            ImageContent(type="image", data=screenshot, mimeType="image/png")
        )
    elif not _only_text_feedback:
        response.append(TextContent(type="text", text=_SCREENSHOT_UNAVAILABLE_MESSAGE))
    return response


def _run_freecad_operation(
    *,
    operation: OperationFn,
    success_message: MessageFn,
    failure_message: MessageFn,
    log_context: str,
    log_details: str | None = None,
    include_screenshot: bool = True,
) -> ToolResponse:
    """Execute an operation that returns a FreeCAD status dictionary."""
    log_target = f"{log_context}{f' ({log_details})' if log_details else ''}"

    try:
        freecad = get_freecad_connection()
        result = operation(freecad)
    except Exception as exc:  # pragma: no cover - network boundary
        logger.exception("Failed to %s", log_target)
        return [TextContent(type="text", text=f"Failed to {log_context}: {exc}")]

    screenshot = freecad.get_active_screenshot() if include_screenshot else None
    success = bool(result.get("success"))
    formatter = success_message if success else failure_message

    try:
        message = formatter(result)
    except Exception as exc:  # pragma: no cover - defensive formatting guard
        logger.exception("Failed to format response for %s", log_target)
        message = (
            f"Successfully completed {log_context}."
            if success
            else f"Failed to {log_context}: {exc}"
        )

    if not success:
        logger.warning(
            "FreeCAD reported failure during %s: %s",
            log_target,
            result.get("error", message),
        )

    response: ToolResponse = [TextContent(type="text", text=message)]
    return (
        add_screenshot_if_available(response, screenshot)
        if include_screenshot
        else response
    )


def _run_freecad_query(
    *,
    query: QueryFn[T],
    formatter: FormatterFn[T],
    log_context: str,
    log_details: str | None = None,
    include_screenshot: bool = True,
) -> ToolResponse:
    """Execute a FreeCAD query that returns arbitrary data."""
    log_target = f"{log_context}{f' ({log_details})' if log_details else ''}"

    try:
        freecad = get_freecad_connection()
        result = query(freecad)
    except Exception as exc:  # pragma: no cover - network boundary
        logger.exception("Failed to %s", log_target)
        return [TextContent(type="text", text=f"Failed to {log_context}: {exc}")]

    screenshot = freecad.get_active_screenshot() if include_screenshot else None

    try:
        message = formatter(result)
    except Exception as exc:  # pragma: no cover - defensive formatting guard
        logger.exception("Failed to format response for %s", log_target)
        return [TextContent(type="text", text=f"Failed to {log_context}: {exc}")]

    response: ToolResponse = [TextContent(type="text", text=message)]
    return (
        add_screenshot_if_available(response, screenshot)
        if include_screenshot
        else response
    )


async def _collect_tool_summaries() -> list[dict[str, Any]]:
    """Return metadata for every registered tool."""
    tools = await mcp.get_tools()
    summaries: list[dict[str, Any]] = []
    for name, tool in sorted(tools.items(), key=lambda item: item[0]):
        summary: dict[str, Any] = {
            "name": tool.name or name,
            "description": tool.description or "",
        }
        if tool.tags:
            summary["tags"] = sorted(tool.tags)
        if tool.parameters:
            summary["parameters"] = tool.parameters
        if tool.output_schema:
            summary["output_schema"] = tool.output_schema
        summaries.append(summary)
    return summaries


@mcp.tool()
def create_document(ctx: Context, name: str) -> ToolResponse:
    """Create a new document in FreeCAD.

    Args:
        name: The name of the document to create.

    Returns:
        A message indicating the success or failure of the document creation.

    Examples:
        If you want to create a document named "MyDocument", you can use the following data.
        ```json
        {
            "name": "MyDocument"
        }
        ```
    """
    return _run_freecad_operation(
        operation=lambda freecad: freecad.create_document(name),
        success_message=lambda res: (
            f"Document '{res.get('document_name', name)}' created successfully"
        ),
        failure_message=lambda res: (
            f"Failed to create document: {res.get('error', 'Unknown error')}"
        ),
        log_context="create document",
        log_details=name,
        include_screenshot=False,
    )


@mcp.tool()
def create_object(
    ctx: Context,
    doc_name: str,
    obj_type: str,
    obj_name: str,
    analysis_name: str | None = None,
    obj_properties: dict[str, Any] | None = None,
) -> ToolResponse:
    """Create a new object in FreeCAD.
    Object type is starts with "Part::" or "Draft::" or "PartDesign::" or "Fem::".

    Args:
        doc_name: The name of the document to create the object in.
        obj_type: The type of the object to create (e.g. 'Part::Box', 'Part::Cylinder', 'Draft::Circle', 'PartDesign::Body', etc.).
        obj_name: The name of the object to create.
        obj_properties: The properties of the object to create.

    Returns:
        A message indicating the success or failure of the object creation and a screenshot of the object.

    Examples:
        If you want to create a cylinder with a height of 30 and a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCylinder",
            "obj_name": "Cylinder",
            "obj_type": "Part::Cylinder",
            "obj_properties": {
                "Height": 30,
                "Radius": 10,
                "Placement": {
                    "Base": {
                        "x": 10,
                        "y": 10,
                        "z": 0
                    },
                    "Rotation": {
                        "Axis": {
                            "x": 0,
                            "y": 0,
                            "z": 1
                        },
                        "Angle": 45
                    }
                },
                "ViewObject": {
                    "ShapeColor": [0.5, 0.5, 0.5, 1.0]
                }
            }
        }
        ```

        If you want to create a circle with a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCircle",
            "obj_name": "Circle",
            "obj_type": "Draft::Circle",
        }
        ```

        If you want to create a FEM analysis, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemAnalysis",
            "obj_type": "Fem::AnalysisPython",
        }
        ```

        If you want to create a FEM constraint, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMConstraint",
            "obj_name": "FemConstraint",
            "obj_type": "Fem::ConstraintFixed",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "References": [
                    {
                        "object_name": "MyObject",
                        "face": "Face1"
                    }
                ]
            }
        }
        ```

        If you want to create a FEM mechanical material, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemMechanicalMaterial",
            "obj_type": "Fem::MaterialCommon",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Material": {
                    "Name": "MyMaterial",
                    "Density": "7900 kg/m^3",
                    "YoungModulus": "210 GPa",
                    "PoissonRatio": 0.3
                }
            }
        }
        ```

        If you want to create a FEM mesh, you can use the following data.
        The `Part` property is required.
        ```json
        {
            "doc_name": "MyFEMMesh",
            "obj_name": "FemMesh",
            "obj_type": "Fem::FemMeshGmsh",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Part": "MyObject",
                "ElementSizeMax": 10,
                "ElementSizeMin": 0.1,
                "MeshAlgorithm": 2
            }
        }
        ```
    """
    obj_data: dict[str, Any] = {
        "Name": obj_name,
        "Type": obj_type,
        "Properties": obj_properties or {},
    }
    if analysis_name:
        obj_data["Analysis"] = analysis_name

    return _run_freecad_operation(
        operation=lambda freecad: freecad.create_object(doc_name, obj_data),
        success_message=lambda res: (
            f"Object '{res.get('object_name', obj_name)}' created successfully"
        ),
        failure_message=lambda res: (
            f"Failed to create object: {res.get('error', 'Unknown error')}"
        ),
        log_context="create object",
        log_details=f"{doc_name}/{obj_name}",
    )


@mcp.tool()
def edit_object(
    ctx: Context, doc_name: str, obj_name: str, obj_properties: dict[str, Any]
) -> ToolResponse:
    """Edit an object in FreeCAD.
    This tool is used when the `create_object` tool cannot handle the object creation.

    Args:
        doc_name: The name of the document to edit the object in.
        obj_name: The name of the object to edit.
        obj_properties: The properties of the object to edit.

    Returns:
        A message indicating the success or failure of the object editing and a screenshot of the object.
    """
    return _run_freecad_operation(
        operation=lambda freecad: freecad.edit_object(
            doc_name, obj_name, {"Properties": obj_properties}
        ),
        success_message=lambda res: (
            f"Object '{res.get('object_name', obj_name)}' edited successfully"
        ),
        failure_message=lambda res: (
            f"Failed to edit object: {res.get('error', 'Unknown error')}"
        ),
        log_context="edit object",
        log_details=f"{doc_name}/{obj_name}",
    )


@mcp.tool()
def delete_object(ctx: Context, doc_name: str, obj_name: str) -> ToolResponse:
    """Delete an object in FreeCAD.

    Args:
        doc_name: The name of the document to delete the object from.
        obj_name: The name of the object to delete.

    Returns:
        A message indicating the success or failure of the object deletion and a screenshot of the object.
    """
    return _run_freecad_operation(
        operation=lambda freecad: freecad.delete_object(doc_name, obj_name),
        success_message=lambda res: (
            f"Object '{res.get('object_name', obj_name)}' deleted successfully"
        ),
        failure_message=lambda res: (
            f"Failed to delete object: {res.get('error', 'Unknown error')}"
        ),
        log_context="delete object",
        log_details=f"{doc_name}/{obj_name}",
    )


@mcp.tool()
def execute_code(ctx: Context, code: str) -> ToolResponse:
    """Execute arbitrary Python code in FreeCAD.

    Args:
        code: The Python code to execute.

    Returns:
        A message indicating the success or failure of the code execution, the output of the code execution, and a screenshot of the object.
    """
    return _run_freecad_operation(
        operation=lambda freecad: freecad.execute_code(code),
        success_message=lambda res: (
            f"Code executed successfully: {res.get('message', 'No output returned')}"
        ),
        failure_message=lambda res: (
            f"Failed to execute code: {res.get('error', 'Unknown error')}"
        ),
        log_context="execute code",
    )


@mcp.tool()
def get_view(
    ctx: Context,
    view_name: Literal[
        "Isometric",
        "Front",
        "Top",
        "Right",
        "Back",
        "Left",
        "Bottom",
        "Dimetric",
        "Trimetric",
    ],
) -> ToolResponse:
    """Get a screenshot of the active view.

    Args:
        view_name: The name of the view to get the screenshot of.
        The following views are available:
        - "Isometric"
        - "Front"
        - "Top"
        - "Right"
        - "Back"
        - "Left"
        - "Bottom"
        - "Dimetric"
        - "Trimetric"

    Returns:
        A screenshot of the active view.
    """
    try:
        freecad = get_freecad_connection()
    except Exception as exc:  # pragma: no cover - network boundary
        logger.exception("Failed to get view %s", view_name)
        return [TextContent(type="text", text=f"Failed to get view: {exc}")]

    screenshot = freecad.get_active_screenshot(view_name)

    if screenshot is not None and not _only_text_feedback:
        return [ImageContent(type="image", data=screenshot, mimeType="image/png")]
    if screenshot is not None:
        return [TextContent(type="text", text=_TEXT_ONLY_MESSAGE)]
    return [
        TextContent(
            type="text",
            text="Cannot get screenshot in the current view type (such as TechDraw or Spreadsheet)",
        )
    ]


@mcp.tool()
def insert_part_from_library(ctx: Context, relative_path: str) -> ToolResponse:
    """Insert a part from the parts library addon.

    Args:
        relative_path: The relative path of the part to insert.

    Returns:
        A message indicating the success or failure of the part insertion and a screenshot of the object.
    """
    return _run_freecad_operation(
        operation=lambda freecad: freecad.insert_part_from_library(relative_path),
        success_message=lambda res: (
            f"Part inserted from library: {res.get('message', 'Success')}"
        ),
        failure_message=lambda res: (
            f"Failed to insert part from library: {res.get('error', 'Unknown error')}"
        ),
        log_context="insert part from library",
        log_details=relative_path,
    )


@mcp.tool()
def get_objects(ctx: Context, doc_name: str) -> ToolResponse:
    """Get all objects in a document.
    You can use this tool to get the objects in a document to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the objects from.

    Returns:
        A list of objects in the document and a screenshot of the document.
    """
    return _run_freecad_query(
        query=lambda freecad: freecad.get_objects(doc_name),
        formatter=lambda payload: json.dumps(payload, default=str),
        log_context="get objects",
        log_details=doc_name,
    )


@mcp.tool()
def get_object(ctx: Context, doc_name: str, obj_name: str) -> ToolResponse:
    """Get an object from a document.
    You can use this tool to get the properties of an object to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the object from.
        obj_name: The name of the object to get.

    Returns:
        The object and a screenshot of the object.
    """
    return _run_freecad_query(
        query=lambda freecad: freecad.get_object(doc_name, obj_name),
        formatter=lambda payload: json.dumps(payload, default=str),
        log_context="get object",
        log_details=f"{doc_name}/{obj_name}",
    )


@mcp.tool()
def get_parts_list(ctx: Context) -> ToolResponse:
    """Get the list of parts in the parts library addon."""

    return _run_freecad_query(
        query=lambda freecad: freecad.get_parts_list(),
        formatter=lambda parts: (
            json.dumps(parts, default=str)
            if parts
            else "No parts found in the parts library. You must add parts_library addon."
        ),
        log_context="get parts list",
        include_screenshot=False,
    )


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def health_check(_: Request) -> JSONResponse:
    """Simple readiness probe for automation and dashboards."""

    status = "ok"
    freecad_status: dict[str, Any] = {"connected": True}

    try:
        connection = get_freecad_connection()
        if not connection.ping():
            status = "degraded"
            freecad_status.update(
                connected=False,
                message="FreeCAD RPC ping returned False",
            )
    except Exception as exc:  # pragma: no cover - network boundary
        status = "degraded"
        freecad_status.update(connected=False, error=str(exc))

    tool_summaries = await _collect_tool_summaries()
    tools_info = {
        "count": len(tool_summaries),
        "names": [summary["name"] for summary in tool_summaries],
    }

    return JSONResponse(
        {
            "status": status,
            "details": {
                "freecad": freecad_status,
                "tools": tools_info,
            },
        },
        status_code=200 if status == "ok" else 503,
    )


@mcp.custom_route("/docs.json", methods=["GET"])
async def docs_json(_: Request) -> JSONResponse:
    """Machine-readable description of registered MCP tools."""

    tool_summaries = await _collect_tool_summaries()
    return JSONResponse({"tools": tool_summaries})


@mcp.custom_route("/docs", methods=["GET"])
async def docs_page(_: Request) -> HTMLResponse:
    """Minimal HTML explorer for the MCP tools registry."""

    tool_summaries = await _collect_tool_summaries()
    sections = []
    for summary in tool_summaries:
        block = [f"<h2>{html.escape(summary['name'])}</h2>"]
        description = summary.get("description") or "No description provided."
        block.append(f"<p>{html.escape(description)}</p>")

        if tags := summary.get("tags"):
            block.append(
                "<p><strong>Tags:</strong> "
                + ", ".join(html.escape(tag) for tag in tags)
                + "</p>"
            )

        if params := summary.get("parameters"):
            block.append(
                "<details><summary>Parameters schema</summary><pre>"
                + html.escape(json.dumps(params, indent=2))
                + "</pre></details>"
            )

        if output_schema := summary.get("output_schema"):
            block.append(
                "<details><summary>Output schema</summary><pre>"
                + html.escape(json.dumps(output_schema, indent=2))
                + "</pre></details>"
            )

        sections.append("\n".join(block))

    section_markup = [f"  <section>{section}</section>" for section in sections]
    if not section_markup:
        section_markup = ["  <p>No tools registered.</p>"]

    body = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            "  <title>FreeCAD MCP Tools</title>",
            "  <style>body{font-family:system-ui;margin:2rem;}h1{margin-bottom:1rem;}section{margin-bottom:2rem;}details{margin-top:0.5rem;}pre{background:#f4f4f4;padding:0.75rem;border-radius:4px;overflow:auto;}</style>",
            "</head>",
            "<body>",
            "  <h1>FreeCAD MCP Tools</h1>",
            "  <p>Use <code>/docs.json</code> for a machine-readable listing or <code>/healthz</code> for readiness checks.</p>",
            *section_markup,
            "</body>",
            "</html>",
        ]
    )

    return HTMLResponse(body)


@mcp.prompt()
def asset_creation_strategy() -> str:
    return """
Asset Creation Strategy for FreeCAD MCP

When creating content in FreeCAD, always follow these steps:

0. Before starting any task, always use get_objects() to confirm the current state of the document.

1. Utilize the parts library:
   - Check available parts using get_parts_list().
   - If the required part exists in the library, use insert_part_from_library() to insert it into your document.

2. If the appropriate asset is not available in the parts library:
   - Create basic shapes (e.g., cubes, cylinders, spheres) using create_object().
   - Adjust and define detailed properties of the shapes as necessary using edit_object().

3. Always assign clear and descriptive names to objects when adding them to the document.

4. Explicitly set the position, scale, and rotation properties of created or inserted objects using edit_object() to ensure proper spatial relationships.

5. After editing an object, always verify that the set properties have been correctly applied by using get_object().

6. If detailed customization or specialized operations are necessary, use execute_code() to run custom Python scripts.

Only revert to basic creation methods in the following cases:
- When the required asset is not available in the parts library.
- When a basic shape is explicitly requested.
- When creating complex shapes requires custom scripting.
"""


def _normalize_relative_path(path: str) -> str:
    """Ensure the provided path is a relative HTTP path."""
    stripped = path.strip()
    if not stripped:
        raise ValueError("Path cannot be empty")
    if "://" in stripped or stripped.startswith("//"):
        raise ValueError(
            "Path must be relative and must not include a scheme or network location"
        )
    if "?" in stripped or "#" in stripped:
        raise ValueError("Path must not contain query strings or fragments")
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    return stripped


def _set_only_text_feedback(enabled: bool) -> None:
    global _only_text_feedback
    _only_text_feedback = enabled
    logger.info("Only text feedback: %s", _only_text_feedback)


def create_app(
    *,
    only_text_feedback: bool = False,
    sse_path: str = DEFAULT_SSE_PATH,
    message_path: str = DEFAULT_MESSAGE_PATH,
    debug: bool = False,
) -> FastAPI:
    """Create a FastAPI application exposing the FastMCP server over SSE."""
    normalized_sse_path = _normalize_relative_path(sse_path)
    normalized_message_path = _normalize_relative_path(message_path)

    _set_only_text_feedback(only_text_feedback)

    sse_app = create_sse_app(
        server=mcp,
        message_path=normalized_message_path,
        sse_path=normalized_sse_path,
        debug=debug,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with sse_app.router.lifespan_context(sse_app):
            yield

    app = FastAPI(lifespan=lifespan, debug=debug)

    @app.middleware("http")
    async def log_requests_middleware(request: Request, call_next):
        logger.info(f"Incoming request: {request.method} {request.url}")
        body_bytes = await request.body()
        if body_bytes:
            # Log request body
            try:
                body_json = json.loads(body_bytes)
                logger.info(f"Request body: {json.dumps(body_json, indent=2)}")
            except json.JSONDecodeError:
                logger.info(f"Request body: {body_bytes.decode(errors='ignore')}")

        # Because reading the request body consumes it, we need to create a new
        # 'receive' awaitable that returns the body, so the endpoint can read it.
        async def receive():
            return {"type": "http.request", "body": body_bytes}

        request._receive = receive
        response = await call_next(request)
        return response

    app.mount("/", sse_app)
    app.state.fastmcp_server = mcp
    app.state.sse_path = normalized_sse_path
    app.state.message_path = normalized_message_path

    return app


def main() -> None:
    """Run the SSE server using FastAPI and uvicorn."""
    parser = argparse.ArgumentParser(description="Run the FreeCAD MCP SSE server")
    parser.add_argument(
        "--only-text-feedback",
        action="store_true",
        help="Disable screenshot feedback and respond with text only",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host interface for the uvicorn server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="TCP port for the uvicorn server",
    )
    parser.add_argument(
        "--sse-path",
        default=DEFAULT_SSE_PATH,
        help="Relative path that clients use to establish SSE connections",
    )
    parser.add_argument(
        "--message-path",
        default=DEFAULT_MESSAGE_PATH,
        help="Relative path where clients POST MCP messages",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Log level forwarded to uvicorn",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable FastAPI debug mode for additional diagnostics",
    )
    args = parser.parse_args()

    _maybe_enable_debugpy()

    try:
        app = create_app(
            only_text_feedback=args.only_text_feedback,
            sse_path=args.sse_path,
            message_path=args.message_path,
            debug=args.debug,
        )
    except ValueError as exc:  # pragma: no cover - defensive guard around CLI usage
        parser.error(str(exc))
        return

    logger.info(
        "Starting FreeCAD MCP SSE server at %s:%s (SSE path %s, message path %s)",
        args.host,
        args.port,
        app.state.sse_path,
        app.state.message_path,
    )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


def _maybe_enable_debugpy() -> None:
    port_value = os.getenv("FREECAD_MCP_DEBUGPY_PORT")
    if not port_value:
        return

    host = os.getenv("FREECAD_MCP_DEBUGPY_HOST", "127.0.0.1")
    wait = os.getenv("FREECAD_MCP_DEBUGPY_WAIT_FOR_CLIENT", "1").lower() not in {
        "0",
        "false",
        "no",
    }

    try:
        port = int(port_value)
    except ValueError:
        logger.error("Invalid FREECAD_MCP_DEBUGPY_PORT value: %s", port_value)
        return

    try:
        import debugpy
    except Exception as exc:  # pragma: no cover - diagnostic logging only
        logger.error("Failed to import debugpy: %s", exc)
        return

    try:
        debugpy.listen((host, port))
    except Exception as exc:  # pragma: no cover - diagnostic logging only
        logger.error("Failed to start debugpy listener on %s:%s: %s", host, port, exc)
        return

    logger.info("Waiting for debugger attach on %s:%s", host, port)
    if wait:
        debugpy.wait_for_client()


if __name__ == "__main__":
    main()
