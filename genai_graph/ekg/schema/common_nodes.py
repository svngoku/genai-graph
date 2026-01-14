from pydantic import BaseModel, Field

from genai_graph.core.graph_schema import GraphNode
from genai_graph.ekg.baml_client.types import Customer, Opportunity, Person


class FileMetadata(BaseModel):
    """Simple file provenance information."""

    source: str = Field(..., description="Source of the file from which the data was extracted")


class WinLoss(BaseModel):
    """Win / loss outcome information."""

    result: str = Field(..., description="Win/Loss outcome (win|loss|unknown)")
    reason: str | None = Field(None, description="Short reason for the outcome")


def get_common_nodes() -> list[GraphNode]:
    return [
        GraphNode(
            node_class=Opportunity,
            name_from="name",
            description="Core opportunity information with financial metrics embedded",
            deduplication_key="opportunity_id",
            index_fields=["name", "status"],
        ),
        GraphNode(
            node_class=Customer,
            name_from="name",
            description="Customer organization details",
            index_fields=["name"],
        ),
        GraphNode(
            node_class=Person,
            name_from="name",
            deduplication_key="name",
            description="Individual contacts and team members",
        ),
    ]
