"""Prefect flow for generating fake Rainbow JSON files from CRM data.

This flow reads CRM export data (Excel file) and uses BAML to generate
fake Rainbow JSON files based on the opportunity information.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from genai_tk.extra.structured.baml_util import baml_invoke
from genai_tk.utils.file_patterns import resolve_config_path
from loguru import logger
from prefect import flow, task
from prefect.task_runners import ConcurrentTaskRunner  # type: ignore[attr-defined]
from pydantic import BaseModel, Field
from upath import UPath


class FakeRainbowTaskResult(BaseModel):
    """Result of a single fake Rainbow JSON generation task."""

    opportunity_id: str
    output_path: str


class FakeRainbowFlowResult(BaseModel):
    """Result of the CRM fake Rainbow generation flow."""

    total_requested: int
    total_generated: int
    output_dir: str
    output_files: list[str] = Field(default_factory=list)
    timestamp: str


@task
async def _generate_fake_rainbow_task(
    row_data: dict[str, Any],
    output_dir: str,
    config_name: str,
    llm: str | None,
    force: bool,
) -> FakeRainbowTaskResult:
    """Generate a single fake Rainbow JSON file from CRM row data.

    Args:
        row_data: Dictionary containing CRM row data with keys like
            "Atos Opportunity ID", "Opportunity Name", etc.
        output_dir: Directory to write output JSON files
        config_name: Configuration name from YAML config
        llm: Optional LLM identifier
        force: Overwrite existing files if True

    Returns:
        FakeRainbowTaskResult with opportunity_id and output_path
    """
    opportunity_id = str(row_data.get("Atos Opportunity ID", ""))
    account_name = str(row_data.get("Account Name", ""))

    # Generate filename from opportunity_id and account_name
    # Sanitize the account name for filename
    safe_account_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in account_name)
    filename = f"{opportunity_id}_{safe_account_name}.json"

    output_path = UPath(output_dir) / "fake" / filename

    # Check if file already exists
    if output_path.exists() and not force:
        logger.info(f"Skipping - file already exists: {output_path}")
        return FakeRainbowTaskResult(opportunity_id=opportunity_id, output_path=str(output_path))

    # Build input text from CRM data for BAML function
    input_text = (
        f"Project: {row_data.get('Opportunity Name', 'Unknown')}; "
        f"Industry: {row_data.get('Industry', 'Unknown')}; "
        f"Sub-Industry: {row_data.get('Sub-Industry', '')}; "
        f"Client: {row_data.get('Account Name', 'Unknown')}; "
        f"Sales Lead: {row_data.get('Client Leader', 'Unknown')} from Atos team; "
        f"Reason: {row_data.get('Reason', '')}"
    )

    logger.info(f"Generating fake Rainbow data for opportunity {opportunity_id}")
    logger.debug(f"Input text: {input_text}")

    # Call BAML FakeRainbowJson function
    params: dict[str, Any] = {"__input__": input_text}
    result = await baml_invoke("FakeRainbowJson", params, config_name, llm)

    # Serialize result to JSON
    if isinstance(result, BaseModel):
        json_text = result.model_dump_json(indent=2)
    else:
        json_text = json.dumps(result, indent=2, default=str)

    # Write output file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_text, encoding="utf-8")

    logger.success(f"Generated fake Rainbow JSON: {output_path}")

    return FakeRainbowTaskResult(opportunity_id=opportunity_id, output_path=str(output_path))


@flow(name="crm_fake_rainbow_generation", task_runner=ConcurrentTaskRunner())  # type: ignore[call-arg]
def crm_fake_rainbow_flow(
    crm_file_path: str,
    output_dir: str,
    *,
    num_files: int = 5,
    config_name: str = "default",
    llm: str | None = None,
    force: bool = False,
) -> FakeRainbowFlowResult:
    """Generate fake Rainbow JSON files from CRM export data.

    Args:
        crm_file_path: Path to the CRM export Excel file (supports config variables)
        output_dir: Directory to write output JSON files (supports config variables)
        num_files: Number of fake files to generate (default: 5)
        config_name: Configuration name from YAML config
        llm: Optional LLM identifier
        force: Overwrite existing files if True

    Returns:
        FakeRainbowFlowResult with generation statistics
    """
    # Resolve paths
    resolved_crm_path = resolve_config_path(crm_file_path)
    resolved_output_dir = resolve_config_path(output_dir)

    logger.info(f"Reading CRM export from: {resolved_crm_path}")
    logger.info(f"Generating {num_files} fake Rainbow JSON files to: {resolved_output_dir}")

    # Read Excel file
    crm_path = UPath(resolved_crm_path)
    if not crm_path.exists():
        raise FileNotFoundError(f"CRM export file not found: {resolved_crm_path}")

    df = pd.read_excel(crm_path)

    # Check if we have enough rows
    if len(df) < num_files:
        logger.warning(
            f"CRM export has only {len(df)} rows, but {num_files} files requested. Will generate {len(df)} files."
        )
        num_files = len(df)

    # Required columns
    required_columns = [
        "Atos Opportunity ID",
        "Opportunity Name",
        "Reason",
        "Industry",
        "Client Leader",
        "Account Name",
        "Sub-Industry",
    ]

    # Validate columns exist
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns in CRM export: {missing_columns}")

    # Extract first n rows
    rows_to_process = df.head(num_files)

    logger.info(f"Processing {len(rows_to_process)} CRM rows in parallel...")

    # Submit tasks in parallel
    futures = []
    for _, row in rows_to_process.iterrows():
        row_dict = row.to_dict()
        future = _generate_fake_rainbow_task.submit(
            row_data=row_dict,
            output_dir=resolved_output_dir,
            config_name=config_name,
            llm=llm,
            force=force,
        )
        futures.append(future)

    # Wait for all tasks to complete
    results = [future.result() for future in futures]  # type: ignore[misc]

    # Collect statistics
    success_count = len(results)
    output_files = [r.output_path for r in results]

    logger.success(f"Successfully generated {success_count} fake Rainbow JSON files")

    return FakeRainbowFlowResult(
        total_requested=num_files,
        total_generated=success_count,
        output_dir=resolved_output_dir,
        output_files=output_files,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
