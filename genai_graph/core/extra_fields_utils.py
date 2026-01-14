from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel

from genai_graph.core.graph_schema import GraphNode


def apply_extra_fields(
    item_data: Dict[str, Any], node_info: GraphNode, model: BaseModel, item: Any, source_key: str | None
) -> None:
    """Ensure a simple ``metadata`` dict and attach ``source`` for the root node.

    Rules:
    * If the node class defines a ``metadata`` field and it is missing, create
      an empty ``{}``.
    * We assume ``metadata`` is already a ``dict | None`` on the models; no
      legacy JSON string parsing is performed.
    * For the root model only, if a ``source_key`` is provided, set
      ``metadata["source"] = source_key`` when not already present.
    """

    # 1) Ensure ``metadata`` exists as a dict when declared on the node class.
    if hasattr(node_info.node_class, "model_fields") and "metadata" in node_info.node_class.model_fields:
        metadata = item_data.get("metadata")
        if metadata is None:
            item_data["metadata"] = {}
        elif not isinstance(metadata, dict):
            # We no longer try to coerce arbitrary legacy types; keep it simple.
            item_data["metadata"] = {}

    # 2) Attach provenance on the root model instance when a source key is provided.
    if not source_key:
        return

    if node_info.node_class is not type(model):
        return

    if "metadata" not in item_data or not isinstance(item_data["metadata"], dict):
        item_data["metadata"] = {}

    metadata = item_data["metadata"]
    metadata.setdefault("source", source_key)
