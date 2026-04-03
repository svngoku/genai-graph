"""Example script to demonstrate warnings report generation."""

from genai_graph.kg.export.warnings_report import generate_warnings_markdown

# Sample warnings from a real KG creation scenario
sample_warnings = [
    "Multiple relationships defined between Opportunity and Customer: HAS_CUSTOMER, FOR_CUSTOMER",
    "Multiple relationships defined between Customer and Person: HAS_CONTACT, CONTACT_FOR",
    "Class Partner is referenced in relationships but has no GraphNode",
    "Class Document is referenced in relationships but has no GraphNode",
    "No field paths found for UnusedNode in the root model structure; this node may be orphaned.",
    "Schema creation failed for subgraph stratnav_graph: Missing required field 'name'",
]

# Generate the markdown report
markdown_report = generate_warnings_markdown(sample_warnings)

# Save to a file for demonstration
output_file = "/tmp/sample-warnings-report.md"
with open(output_file, "w") as f:
    f.write(markdown_report)

print(f"Sample warnings report generated: {output_file}")
print("\n" + "=" * 80)
print("PREVIEW.")
print("=" * 80 + "\n")
print(markdown_report[:2000])
print("\n... (truncated) ...")
