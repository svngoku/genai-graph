from pydantic import BaseModel, Field

from genai_graph.ekg.baml_client.types import Customer as BamlCustomer
from genai_graph.ekg.baml_client.types import Opportunity as BamlOpportunity
from genai_graph.ekg.baml_client.types import Person
from genai_graph.kg.schema import GraphNode


class FileMetadata(BaseModel):
    """Simple file provenance information."""

    source: str = Field(..., description="Source of the file from which the data was extracted")


class WinLoss(BaseModel):
    """Win / loss outcome information."""

    result: str = Field(..., description="Win/Loss outcome (win|loss|unknown)")
    reason: str | None = Field(None, description="Short reason for the outcome")


class GeoLocation(BaseModel):
    """Geographic location."""

    name: str = Field(..., description="Location name")
    country: str = Field(default="", description="Country code or name")


class L3Service(BaseModel):
    """Level 3 service offering."""

    code: str = Field(..., description="Service code identifier (L3Code)")
    name: str = Field(default="", description="Service name")
    description: str = Field(default="", description="Description of the service")
    service_type: str = Field(default="", description="Type of service")


class Customer(BamlCustomer):
    """Customer with extended fields for location and services.

    Extends the base BAML Customer with:
    - location: Geographic location of the customer
    - services: L3 services associated with the customer
    - metadata: Provenance and source tracking information
    """

    location: GeoLocation | None = None
    services: list[L3Service] = Field(default_factory=list)
    metadata: dict[str, str] | None = None


class Opportunity(BamlOpportunity):
    """Opportunity with extended fields for win/loss tracking and provenance.

    Extends the base BAML Opportunity with:
    - customer: Override to use our extended Customer class (not BamlCustomer)
    - lead: The sales lead person for the opportunity
    - win_loss: The outcome of the opportunity (win/loss/unknown)
    - metadata: Provenance and source tracking information
    """

    # Override customer field to use our extended Customer class
    customer: "Customer" = Field(description="Client or customer information")  # type: ignore[assignment]
    lead: Person | None = None
    win_loss: WinLoss | None = None
    metadata: dict[str, str] | None = None


def get_common_nodes() -> list[GraphNode]:
    return [
        GraphNode(
            node_class=Opportunity,
            name_from="name",
            key_from="opportunity_id",  # Use opportunity_id as primary key
            description="Core opportunity information with financial metrics embedded",
            index_fields=["name", "status"],
        ),
        GraphNode(
            node_class=Customer,
            name_from="name",
            key_from="name",  # TODO: replace with iris_code when available
            description="Customer organization details",
            index_fields=["name"],
        ),
        GraphNode(
            node_class=Person,
            name_from="name",
            key_from="name",  # Use name as primary key for deduplication
            description="Individual contacts and team members",
        ),
    ]
