# KG Create Command Enhancements

## Summary

Enhanced the `kg create` CLI command to support multiple KG configurations in a single execution.

## Changes Made

### Modified File
- [genai_graph/core/commands_ekg.py](../genai_graph/core/commands_ekg.py)

### New Features

1. **`--kg` Parameter**: Specify one or more KG configurations to create
   ```bash
   cli kg create --kg simple
   cli kg create --kg simple --kg test1_with_db
   ```

2. **`--all-graphs` Flag**: Create all KG configurations defined in ekg.yaml
   ```bash
   cli kg create --all-graphs
   ```

3. **Backward Compatibility**: Still supports the original behavior using `kg_config` from configuration
   ```bash
   cli kg create  # Uses kg_config from config
   ```

## Usage Examples

```bash
# Use default kg_config from configuration
cli kg create

# Create a specific KG configuration
cli kg create --kg simple

# Create multiple specific KG configurations
cli kg create --kg simple --kg test1_with_db --kg db_only

# Create all defined KG configurations
cli kg create --all-graphs

# Combine with existing options
cli kg create --kg simple --no-delete-first --no-export-html
cli kg create --all-graphs --delete-first
```

## Implementation Details

### Configuration Access
- Uses `global_config()` to retrieve KG configurations from `ekg.yaml`
- Accesses `kg_configs` dictionary via `cfg.get_dict("kg_configs")`
- Does NOT read YAML files directly

### Processing Flow
1. Determine which KG configs to process:
   - If `--all-graphs`: Get all configs from `global_config().get_dict("kg_configs")`
   - If `--kg` specified: Use provided list
   - Otherwise: Use default `kg_config` from manager profile

2. Iterate through each KG configuration:
   - Run `create_kg_flow()` for each config
   - Track results and failures
   - Continue processing even if one config fails

3. Display summary for multiple configs:
   - Show successful creations with statistics
   - Show failed creations with error messages
   - Exit with code 1 if any failures occurred

### Error Handling
- Individual KG creation failures don't stop the entire process
- Failed configs are tracked and reported in the summary
- Exit code 1 if any configs failed, 0 if all succeeded

### Output Enhancements
- Clear visual separation between different KG configs
- Individual progress and results for each config
- Consolidated summary when processing multiple configs
- HTML export links shown for each successfully created KG

## Configuration Structure

The command reads from `config/ekg.yaml` which defines available KG configurations:

```yaml
kg_configs:
  simple:
    subgraphs: [...]
  test1_with_db:
    subgraphs: [...]
  db_only:
    subgraphs: [...]
  # ... more configs
```

## Notes

- All KG configs are read from `global_config()` - no direct YAML file reading
- The command maintains full backward compatibility with existing usage
- Multiple KG creation allows for batch processing workflows
- Each KG gets its own database path and HTML export

## Warnings Reporting

### Structured Markdown Reports

The KG creation process now generates comprehensive warnings reports in Markdown format, in addition to the plain text log file. This report is automatically created at the end of each KG creation.

**File Location**: `{kg_outputs}/{profile}-{tag}-warnings.md`

### Report Features

1. **Categorized Warnings**: Groups related warnings into categories:
   - 🔄 **Duplicate Relationships**: Multiple relationship types between node pairs
   - ⚠️ **Missing Node Configurations**: Referenced nodes without GraphNode configs
   - 🔗 **Orphaned Nodes**: Nodes not reachable from root model
   - ❌ **Schema Creation Failures**: Subgraph schema errors
   - ℹ️ **Other Warnings**: Miscellaneous warnings

2. **Structured Tables**: Each category includes:
   - Count of warnings in that category
   - Description explaining the issue
   - Actionable suggestions for resolution
   - Detailed tables with structured information
   - Expandable raw warning messages

3. **Cross-Graph Detection**: The report analyzes warnings across all subgraphs, making it easier to spot issues that span multiple graph definitions (e.g., duplicate relationships between the same nodes defined in different files).

### Example Warning Detection

For instance, if you have:
- `HAS_CUSTOMER` relationship from `Opportunity` to `Customer` in `rainbow_review.py`
- `FOR_CUSTOMER` relationship from `Opportunity` to `Customer` in `crm_export.py`

The warnings report will detect this and display:

| From Node | To Node | Relationship Names |
|-----------|---------|-------------------|
| `Opportunity` | `Customer` | HAS_CUSTOMER, FOR_CUSTOMER |

With a suggestion to consolidate to a single, semantically clear relationship type.

### Accessing the Report

The warnings report is:
- Automatically generated at the end of KG creation
- Linked in the info markdown file (`{profile}-{tag}-info.md`)
- Displayed in the KG creation summary
- Accessible via the KgManager's `warnings_md_path` property

### Integration with Info Report

The info markdown file now includes a direct link to the warnings report:

```markdown
- **Warnings Report**: [📊 {profile}-{tag}-warnings.md]({profile}-{tag}-warnings.md)
```

This makes it easy to navigate from the main info page to the detailed warnings analysis.
