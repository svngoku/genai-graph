"""Generate comprehensive warnings reports for KG creation.

This module analyzes warnings collected during KG creation and generates
a structured Markdown report with grouped warnings, explanations, and suggestions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel


class WarningCategory(BaseModel):
    """Category of related warnings with analysis and suggestions."""

    category: str
    """Short name for the category (e.g., 'duplicate_relationships')"""

    title: str
    """Human-readable title for displaying in the report"""

    description: str
    """Explanation of what this category means"""

    suggestion: str
    """Actionable suggestion for resolving these warnings"""

    warnings: list[str]
    """List of raw warning messages in this category"""

    examples: list[dict[str, str]] | None = None
    """Optional structured examples extracted from warnings"""


class WarningsReport(BaseModel):
    """Complete warnings report with analysis and categorization."""

    total_warnings: int
    """Total number of warnings collected"""

    categories: list[WarningCategory]
    """Categorized and analyzed warnings"""

    uncategorized: list[str]
    """Warnings that didn't match any category"""


def categorize_warnings(warnings: list[str]) -> WarningsReport:
    """Categorize and analyze warnings into structured groups.

    Args:
        warnings: Raw warning messages collected during KG creation

    Returns:
        WarningsReport with categorized warnings and analysis
    """
    categories: dict[str, dict[str, Any]] = defaultdict(lambda: {"warnings": []})

    # Pattern matchers for different warning types
    patterns = {
        "multiple_relationships": r"Multiple relationships defined between (\w+) and (\w+): (.+)",
        "missing_node": r"Class (\w+) is referenced in relationships but has no GraphNode",
        "orphaned_node": r"No field paths found for (\w+) in the root model structure",
        "embedded_field": r"Embedded class (\w+) not found in (\w+)",
        "field_type_mismatch": r"Field '(\w+)' in (\w+) has type mismatch",
        "schema_creation_failed": r"Schema creation failed for subgraph (.+?):",
        "validation_warning": r"Graph schema validation:",
    }

    for warning in warnings:
        categorized = False

        # Check for duplicate/multiple relationships
        match = re.search(patterns["multiple_relationships"], warning)
        if match:
            from_node, to_node, rel_names = match.groups()
            key = "duplicate_relationships"
            if key not in categories:
                categories[key] = {
                    "warnings": [],
                    "examples": [],
                }
            categories[key]["warnings"].append(warning)
            categories[key]["examples"].append(
                {
                    "from_node": from_node,
                    "to_node": to_node,
                    "relationships": rel_names,
                }
            )
            categorized = True

        # Check for missing node configurations
        if not categorized:
            match = re.search(patterns["missing_node"], warning)
            if match:
                class_name = match.group(1)
                key = "missing_nodes"
                if key not in categories:
                    categories[key] = {
                        "warnings": [],
                        "examples": [],
                    }
                categories[key]["warnings"].append(warning)
                categories[key]["examples"].append({"class": class_name})
                categorized = True

        # Check for orphaned nodes
        if not categorized:
            match = re.search(patterns["orphaned_node"], warning)
            if match:
                class_name = match.group(1)
                key = "orphaned_nodes"
                if key not in categories:
                    categories[key] = {
                        "warnings": [],
                        "examples": [],
                    }
                categories[key]["warnings"].append(warning)
                categories[key]["examples"].append({"class": class_name})
                categorized = True

        # Check for schema creation failures
        if not categorized:
            match = re.search(patterns["schema_creation_failed"], warning)
            if match:
                subgraph = match.group(1)
                key = "schema_failures"
                if key not in categories:
                    categories[key] = {
                        "warnings": [],
                        "examples": [],
                    }
                categories[key]["warnings"].append(warning)
                categories[key]["examples"].append({"subgraph": subgraph})
                categorized = True

        # Uncategorized warnings
        if not categorized:
            key = "other"
            if key not in categories:
                categories[key] = {"warnings": []}
            categories[key]["warnings"].append(warning)

    # Build structured categories with metadata
    category_metadata = {
        "duplicate_relationships": {
            "title": "🔄 Duplicate Relationships",
            "description": "Multiple relationship types defined between the same pair of node types. "
            "This can lead to semantic ambiguity about which relationship to use in queries.",
            "suggestion": "Review the relationship names and consolidate to a single, clear relationship type. "
            "Choose the most semantically meaningful name or refactor your schema to use different intermediate nodes.",
        },
        "missing_nodes": {
            "title": "⚠️ Missing Node Configurations",
            "description": "Node classes are referenced in relationships but don't have GraphNode configurations. "
            "This will cause ingestion failures.",
            "suggestion": "Add GraphNode configurations for these classes in your schema definition. "
            "Ensure every class used in a GraphRelation has a corresponding GraphNode.",
        },
        "orphaned_nodes": {
            "title": "🔗 Orphaned Nodes",
            "description": "Node configurations exist but no field paths were found connecting them to the root model. "
            "These nodes won't be populated during ingestion.",
            "suggestion": "Verify that these nodes are reachable from your root model through field paths. "
            "If they're intentionally standalone, consider using explicit field_paths or marking them as explicitly_defined=True.",
        },
        "schema_failures": {
            "title": "❌ Schema Creation Failures",
            "description": "One or more subgraphs failed during schema creation. This usually indicates data model issues.",
            "suggestion": "Check the detailed error messages for each subgraph. Common issues include missing dependencies, "
            "incorrect field references, or validation failures in your Pydantic models.",
        },
        "other": {
            "title": "ℹ️ Other Warnings",
            "description": "Miscellaneous warnings that don't fit into standard categories.",
            "suggestion": "Review each warning individually and address based on the specific message.",
        },
    }

    # Build final categorized list
    categorized_list: list[WarningCategory] = []
    uncategorized: list[str] = []

    for key, data in categories.items():
        if key in category_metadata:
            meta = category_metadata[key]
            categorized_list.append(
                WarningCategory(
                    category=key,
                    title=meta["title"],
                    description=meta["description"],
                    suggestion=meta["suggestion"],
                    warnings=data["warnings"],
                    examples=data.get("examples"),
                )
            )
        else:
            uncategorized.extend(data["warnings"])

    return WarningsReport(
        total_warnings=len(warnings),
        categories=categorized_list,
        uncategorized=uncategorized,
    )


def generate_warnings_markdown(warnings: list[str]) -> str:
    """Generate a comprehensive Markdown report for warnings.

    Args:
        warnings: List of warning messages

    Returns:
        Markdown-formatted report with tables and grouping
    """
    if not warnings:
        return _generate_no_warnings_report()

    report = categorize_warnings(warnings)

    lines: list[str] = [
        "# Knowledge Graph Warnings Report",
        "",
        f"**Total Warnings:** {report.total_warnings}",
        "",
        "This report groups warnings by category with explanations and suggestions for resolution.",
        "",
        "---",
        "",
    ]

    # Generate sections for each category
    for category in report.categories:
        lines.append(f"## {category.title}")
        lines.append("")
        lines.append(f"**Count:** {len(category.warnings)} warning(s)")
        lines.append("")

        # Description
        lines.append("### 📋 Description")
        lines.append("")
        lines.append(category.description)
        lines.append("")

        # Suggestion
        lines.append("### 💡 Suggestion")
        lines.append("")
        lines.append(category.suggestion)
        lines.append("")

        # Examples table for structured data
        if category.examples:
            lines.append("### 📊 Details")
            lines.append("")

            if category.category == "duplicate_relationships":
                lines.append("| From Node | To Node | Relationship Names |")
                lines.append("|-----------|---------|-------------------|")
                for ex in category.examples:
                    lines.append(f"| `{ex['from_node']}` | `{ex['to_node']}` | {ex['relationships']} |")

            elif category.category in ["missing_nodes", "orphaned_nodes"]:
                lines.append("| Node Class |")
                lines.append("|------------|")
                for ex in category.examples:
                    lines.append(f"| `{ex['class']}` |")

            elif category.category == "schema_failures":
                lines.append("| Subgraph Name |")
                lines.append("|---------------|")
                for ex in category.examples:
                    lines.append(f"| `{ex['subgraph']}` |")

            lines.append("")

        # Raw warnings section
        lines.append("### 📝 Raw Warnings")
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Click to expand full warning messages</summary>")
        lines.append("")
        lines.append("```")
        for warning in category.warnings:
            lines.append(warning)
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Uncategorized warnings
    if report.uncategorized:
        lines.append("## ℹ️ Uncategorized Warnings")
        lines.append("")
        lines.append(f"**Count:** {len(report.uncategorized)} warning(s)")
        lines.append("")
        for warning in report.uncategorized:
            lines.append(f"- {warning}")
        lines.append("")

    # Summary and next steps
    lines.extend(
        [
            "---",
            "",
            "## 📌 Next Steps",
            "",
            "1. **Review Duplicate Relationships**: Consolidate relationship types between node pairs",
            "2. **Add Missing Configurations**: Ensure all referenced nodes have GraphNode configs",
            "3. **Check Field Paths**: Verify orphaned nodes are reachable from root models",
            "4. **Fix Schema Errors**: Address any schema creation failures",
            "",
            "For more information, see:",
            "- [Graph Construction Guide](../../../docs/graph_construction.md)",
            "- [Schema Documentation](../../../docs/kg_explorer.md)",
            "",
        ]
    )

    return "\n".join(lines)


def _generate_no_warnings_report() -> str:
    """Generate a success report when there are no warnings."""
    return """# Knowledge Graph Warnings Report

✅ **No warnings detected!**

Your knowledge graph was created successfully with no issues.

---

**Summary:**
- All node configurations are valid
- All relationships are properly defined
- No schema validation issues
- All subgraphs loaded successfully

Keep up the great work! 🎉
"""
