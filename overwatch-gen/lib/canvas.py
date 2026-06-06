"""
canvas.py — Obsidian .canvas JSON builder.

Obsidian canvas format: JSON with "nodes" and "edges" arrays.
Node types used here: "text" (inline markdown content).

Usage:
    from overwatch_gen.lib.canvas import Canvas

    c = Canvas()
    c.add_node("n1", x=0, y=0, width=300, height=100, text="## L1 Physical")
    c.add_node("n2", x=400, y=0, width=300, height=100, text="## L2 Data Link")
    c.add_edge("n1", "n2", label="feeds into")
    json_str = c.to_json()
    # Write to architecture-vault/01-L1-physical/overview.canvas

Output is deterministic: nodes and edges are sorted by id/fromNode before
serialization so repeated runs produce byte-identical JSON.
"""

import json
from typing import Optional


class CanvasError(Exception):
    """Raised for invalid canvas operations."""


class Canvas:
    """
    Builder for an Obsidian .canvas file.

    Nodes have type "text" (markdown content rendered inline in Obsidian).
    Edges connect two node IDs with an optional label and color.
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}  # id -> node dict
        self._edges: list[dict] = []

    def add_node(
        self,
        id: str,
        x: int,
        y: int,
        width: int,
        height: int,
        text: str,
        color: Optional[str] = None,
    ) -> "Canvas":
        """
        Add a text node to the canvas.

        Args:
            id:     Unique node identifier (used in edges). Must be unique.
            x, y:   Top-left position in canvas coordinates.
            width, height: Dimensions in pixels.
            text:   Markdown text content displayed inside the node.
            color:  Optional Obsidian color code (e.g. "1" through "6").

        Returns:
            self, for method chaining.

        Raises:
            CanvasError: If id is already in use.
        """
        if id in self._nodes:
            raise CanvasError(f"Node id {id!r} already exists in canvas.")
        node: dict = {
            "id": id,
            "type": "text",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "text": text,
        }
        if color is not None:
            node["color"] = color
        self._nodes[id] = node
        return self

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        label: Optional[str] = None,
        color: Optional[str] = None,
    ) -> "Canvas":
        """
        Add a directed edge between two nodes.

        Args:
            from_id: Source node ID.
            to_id:   Target node ID.
            label:   Optional text label shown on the edge.
            color:   Optional Obsidian color code.

        Returns:
            self, for method chaining.

        Raises:
            CanvasError: If from_id or to_id do not exist in the canvas.
        """
        if from_id not in self._nodes:
            raise CanvasError(
                f"Edge source {from_id!r} not found. Add the node first."
            )
        if to_id not in self._nodes:
            raise CanvasError(
                f"Edge target {to_id!r} not found. Add the node first."
            )
        edge: dict = {
            "fromNode": from_id,
            "toNode": to_id,
            "fromSide": "right",
            "toSide": "left",
        }
        if label is not None:
            edge["label"] = label
        if color is not None:
            edge["color"] = color
        self._edges.append(edge)
        return self

    def to_json(self) -> str:
        """
        Serialize the canvas to a deterministic JSON string.

        Nodes are sorted by id; edges are sorted by (fromNode, toNode).
        Keys within each object are sorted. Output has 2-space indentation.

        Returns:
            JSON string suitable for writing to a .canvas file.
        """
        nodes_sorted = sorted(self._nodes.values(), key=lambda n: n["id"])
        edges_sorted = sorted(
            self._edges, key=lambda e: (e["fromNode"], e["toNode"])
        )
        canvas_obj = {
            "nodes": nodes_sorted,
            "edges": edges_sorted,
        }
        return json.dumps(canvas_obj, sort_keys=True, indent=2)
