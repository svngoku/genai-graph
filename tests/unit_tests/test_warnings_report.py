"""Tests for warnings report generation."""

from genai_graph.kg.export.warnings_report import categorize_warnings, generate_warnings_markdown


def test_categorize_duplicate_relationships():
    """Test categorization of duplicate relationship warnings."""
    warnings = [
        "Multiple relationships defined between Opportunity and Customer: HAS_CUSTOMER, FOR_CUSTOMER",
        "Multiple relationships defined between Person and Company: WORKS_AT, EMPLOYED_BY",
    ]

    report = categorize_warnings(warnings)

    assert report.total_warnings == 2
    assert len(report.categories) > 0

    # Find the duplicate relationships category
    dup_rel_cat = next((cat for cat in report.categories if cat.category == "duplicate_relationships"), None)
    assert dup_rel_cat is not None
    assert len(dup_rel_cat.warnings) == 2
    assert dup_rel_cat.examples is not None
    assert len(dup_rel_cat.examples) == 2

    # Check first example
    example1 = dup_rel_cat.examples[0]
    assert example1["from_node"] == "Opportunity"
    assert example1["to_node"] == "Customer"
    assert "HAS_CUSTOMER" in example1["relationships"]
    assert "FOR_CUSTOMER" in example1["relationships"]


def test_categorize_missing_nodes():
    """Test categorization of missing node warnings."""
    warnings = [
        "Class Partner is referenced in relationships but has no GraphNode",
        "Class Document is referenced in relationships but has no GraphNode",
    ]

    report = categorize_warnings(warnings)

    assert report.total_warnings == 2

    # Find the missing nodes category
    missing_cat = next((cat for cat in report.categories if cat.category == "missing_nodes"), None)
    assert missing_cat is not None
    assert len(missing_cat.warnings) == 2
    assert missing_cat.examples is not None
    assert len(missing_cat.examples) == 2


def test_categorize_orphaned_nodes():
    """Test categorization of orphaned node warnings."""
    warnings = [
        "No field paths found for UnusedClass in the root model structure; this node may be orphaned.",
    ]

    report = categorize_warnings(warnings)

    assert report.total_warnings == 1

    orphaned_cat = next((cat for cat in report.categories if cat.category == "orphaned_nodes"), None)
    assert orphaned_cat is not None
    assert len(orphaned_cat.warnings) == 1


def test_categorize_schema_failures():
    """Test categorization of schema failure warnings."""
    warnings = [
        "Schema creation failed for subgraph my_graph: Invalid model configuration",
    ]

    report = categorize_warnings(warnings)

    assert report.total_warnings == 1

    schema_cat = next((cat for cat in report.categories if cat.category == "schema_failures"), None)
    assert schema_cat is not None
    assert len(schema_cat.warnings) == 1


def test_categorize_mixed_warnings():
    """Test categorization with multiple warning types."""
    warnings = [
        "Multiple relationships defined between Opportunity and Customer: HAS_CUSTOMER, FOR_CUSTOMER",
        "Class Partner is referenced in relationships but has no GraphNode",
        "No field paths found for UnusedClass in the root model structure; this node may be orphaned.",
        "Some random uncategorized warning",
    ]

    report = categorize_warnings(warnings)

    assert report.total_warnings == 4
    assert len(report.categories) >= 4  # At least 4 categories (including "other")

    # Check that the "other" category exists and has the uncategorized warning
    other_cat = next((cat for cat in report.categories if cat.category == "other"), None)
    assert other_cat is not None
    assert len(other_cat.warnings) == 1
    assert "Some random uncategorized warning" in other_cat.warnings[0]


def test_generate_markdown_with_warnings():
    """Test markdown generation with actual warnings."""
    warnings = [
        "Multiple relationships defined between Opportunity and Customer: HAS_CUSTOMER, FOR_CUSTOMER",
        "Class Partner is referenced in relationships but has no GraphNode",
    ]

    markdown = generate_warnings_markdown(warnings)

    # Check key sections exist
    assert "# Knowledge Graph Warnings Report" in markdown
    assert "**Total Warnings:** 2" in markdown
    assert "🔄 Duplicate Relationships" in markdown
    assert "⚠️ Missing Node Configurations" in markdown
    assert "| From Node | To Node | Relationship Names |" in markdown
    assert "## 📌 Next Steps" in markdown


def test_generate_markdown_no_warnings():
    """Test markdown generation when there are no warnings."""
    warnings = []

    markdown = generate_warnings_markdown(warnings)

    assert "# Knowledge Graph Warnings Report" in markdown
    assert "✅ **No warnings detected!**" in markdown
    assert "Your knowledge graph was created successfully" in markdown


def test_all_categories_have_metadata():
    """Test that all categories have proper metadata."""
    warnings = [
        "Multiple relationships defined between Opportunity and Customer: HAS_CUSTOMER, FOR_CUSTOMER",
        "Class Partner is referenced in relationships but has no GraphNode",
        "No field paths found for UnusedClass in the root model structure; this node may be orphaned.",
        "Schema creation failed for subgraph my_graph: Error",
    ]

    report = categorize_warnings(warnings)

    # Check all categories have required fields
    for category in report.categories:
        assert category.title
        assert category.description
        assert category.suggestion
        assert len(category.warnings) > 0
