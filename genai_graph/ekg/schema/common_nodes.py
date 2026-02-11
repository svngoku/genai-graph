from pydantic import BaseModel, Field

from genai_graph.ekg.baml_client.types import Customer as BamlCustomer
from genai_graph.ekg.baml_client.types import Geo as BamlGeo
from genai_graph.ekg.baml_client.types import Opportunity as BamlOpportunity
from genai_graph.ekg.baml_client.types import Partner as BamlPartner
from genai_graph.ekg.baml_client.types import Person
from genai_graph.kg.schema import GraphNode


class FileMetadata(BaseModel):
    """Simple file provenance information."""

    source: str = Field(..., description="Source of the file from which the data was extracted")


class WinLoss(BaseModel):
    """Win / loss outcome information."""

    result: str = Field(..., description="Win/Loss outcome (win|loss|unknown)")
    reason: str | None = Field(None, description="Short reason for the outcome")


class L3(BaseModel):
    """Level 3 service offering - primary service unit."""

    name: str = Field(description="Service name")
    code: str | None = Field(default=None, description="Service code")
    description: str | None = Field(default=None, description="Service details")
    service_type: str | None = Field(default=None, description="Type (Managed/Consulting)")
    status: str | None = Field(default=None, description="Status (Active/Deprecated)")
    maturity_level: str | None = Field(default=None, description="Service maturity (1-5)")
    plm_stage: str | None = Field(default=None, description="Product lifecycle stage")
    service_id: int | None = Field(default=None, description="Numeric service ID")
    available_for_new_deals: bool | None = Field(default=None, description="New deal availability")
    allow_in_journeys: bool | None = Field(default=None, description="Journey eligibility")
    show_in_catalog: bool | None = Field(default=None, description="Catalog visibility")
    pre_sales_url: str | None = Field(default=None, description="Pre-sales portal link")
    sales_portal_url: str | None = Field(default=None, description="Sales portal link")
    key_buzz_words: str | None = Field(default=None, description="Related keywords")
    grd_definition: str | None = Field(default=None, description="GRD definition")
    deprecated_at: str | None = Field(default=None, description="Deprecation timestamp")
    deprecation_reason: str | None = Field(default=None, description="Why service deprecated")
    grace_period_ends: str | None = Field(default=None, description="Grace period end date")
    major_version: int | None = Field(default=None, description="Major version number")
    minor_version: int | None = Field(default=None, description="Minor version number")


class Geo(BamlGeo):
    """Geographic region or country (canonical type for deduplication)."""


class Partner(BamlPartner):
    """Partner organization (canonical type for deduplication).

    Extends the base BAML Partner for cross-factory unification.
    - Neo4j/Stratnav: TechnologyPartner nodes are mapped to this type
    - BAML extraction: Partner entities with optional role info

    The p_role_ field (from BamlPartner) becomes a relationship property
    on HAS_PARTNER edges, not stored on the Partner node itself.
    """


class Customer(BamlCustomer):
    """Customer organization with extended fields.

    Extends the base BAML Customer with fields from various sources:
    - Neo4j/Stratnav: iris_code, country, business_line, revenue
    - BAML extraction: location, services
    - Provenance: metadata

    This is the canonical Customer type that should be used across all factories
    to ensure proper node deduplication during graph merging.
    """

    # Fields from Neo4j/Stratnav import (Account)
    iris_code: str | None = Field(default=None, description="IRIS code identifier")
    country: str | None = Field(default=None, description="Headquarters country")
    business_line: str | None = Field(default=None, description="Primary business line")
    revenue: str | None = Field(default=None, description="Annual revenue")

    # Fields from BAML extraction
    location: Geo | None = None
    services: list[L3] = Field(default_factory=list)

    # Provenance tracking
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
        GraphNode(
            node_class=Partner,
            name_from="name",
            key_from="name",
            description="Partner organization (technology vendor, subcontractor, etc.)",
        ),
    ]
