"""
Schema inference service using LLM to map spreadsheet columns to our schema.

Uses Claude to intelligently determine:
1. What entity type each tab represents (contact, account, deal)
2. How columns map to our fields
3. Confidence levels for mappings
"""

import json
import logging
from typing import Any, Optional

from anthropic import AsyncAnthropic

from config import settings

logger = logging.getLogger(__name__)

# Target schemas that sheets can map to
TARGET_SCHEMAS: dict[str, dict[str, str]] = {
    "contact": {
        "name": "Full name of the contact (string)",
        "email": "Email address (string, unique identifier)",
        "phone": "Phone number (string)",
        "title": "Job title (string)",
        "account_name": "Company name - will link to or create Account (string)",
    },
    "account": {
        "name": "Company/organization name (string, required)",
        "domain": "Website domain e.g. acme.com (string)",
        "industry": "Industry sector (string)",
        "employee_count": "Number of employees (integer)",
        "annual_revenue": "Annual revenue in dollars (decimal)",
    },
    "deal": {
        "name": "Deal/opportunity name (string, required)",
        "amount": "Deal value in dollars (decimal)",
        "stage": "Sales stage/status (string)",
        "probability": "Win probability 0-100 (integer)",
        "close_date": "Expected close date (date)",
        "account_name": "Company name - will link to or create Account (string)",
        "contact_email": "Primary contact email - will link to Contact (string)",
    },
}

SYSTEM_PROMPT = """You are a data mapping assistant that analyzes spreadsheet data and determines how columns should map to a CRM schema.

Given sample data from spreadsheet tabs and target schemas, you must:
1. Identify what entity type each tab most likely represents (contact, account, or deal)
2. Map column headers to the appropriate schema fields
3. Note any columns that don't map to our schema
4. Provide a confidence score (0-1) for each tab's overall mapping

Guidelines:
- A tab with email addresses and names is likely contacts
- A tab with company names, industries, or revenue is likely accounts
- A tab with deal values, stages, or close dates is likely deals
- Some tabs may have mixed data (e.g., deals with company names) - use relationship fields like account_name
- Ignore columns that don't map to any field
- Be conservative - only map columns when confident

Return your analysis as valid JSON only, no other text."""

USER_PROMPT_TEMPLATE = """Analyze these spreadsheet tabs and map them to our CRM schema.

## Target Schemas

{schemas_json}

## Spreadsheet Data

{tabs_json}

## Instructions

For each tab, determine:
1. `entity_type`: "contact", "account", or "deal"
2. `confidence`: 0.0 to 1.0 indicating how confident you are
3. `column_mappings`: dict mapping column names to schema field names
4. `ignored_columns`: list of columns that don't map to any field
5. `notes`: brief explanation of your reasoning

Return a JSON object with this structure:
{{
  "mappings": [
    {{
      "tab_name": "string",
      "entity_type": "contact" | "account" | "deal",
      "confidence": 0.0-1.0,
      "column_mappings": {{"Column Name": "field_name", ...}},
      "ignored_columns": ["Column A", ...],
      "notes": "string"
    }}
  ]
}}

Respond with ONLY the JSON object, no other text or markdown."""


async def infer_schema_mapping(tabs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Use LLM to infer schema mappings for spreadsheet tabs.
    
    Args:
        tabs: List of tab data with headers and sample rows
              Each tab has: tab_name, headers, sample_rows
              
    Returns:
        Dict with mappings for each tab including entity_type,
        column_mappings, confidence, and notes
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("[SchemaInference] No ANTHROPIC_API_KEY, using heuristic fallback")
        return _heuristic_mapping(tabs)
    
    # Prepare schemas description
    schemas_json = json.dumps(TARGET_SCHEMAS, indent=2)
    
    # Prepare tabs data (limit sample rows for token efficiency)
    tabs_for_prompt: list[dict[str, Any]] = []
    for tab in tabs:
        tabs_for_prompt.append({
            "tab_name": tab["tab_name"],
            "headers": tab["headers"],
            "sample_rows": tab["sample_rows"][:3],  # Limit to 3 rows
        })
    tabs_json = json.dumps(tabs_for_prompt, indent=2)
    
    # Build user prompt
    user_prompt = USER_PROMPT_TEMPLATE.format(
        schemas_json=schemas_json,
        tabs_json=tabs_json,
    )
    
    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        
        # Extract text from response
        text_content = ""
        for block in response.content:
            if block.type == "text":
                text_content = block.text
                break
        
        # Parse JSON response
        # Try to extract JSON if wrapped in markdown code blocks
        if "```json" in text_content:
            text_content = text_content.split("```json")[1].split("```")[0]
        elif "```" in text_content:
            text_content = text_content.split("```")[1].split("```")[0]
        
        result = json.loads(text_content.strip())
        
        logger.info(
            "[SchemaInference] LLM mapped %d tabs",
            len(result.get("mappings", [])),
        )
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error("[SchemaInference] Failed to parse LLM response: %s", str(e))
        return _heuristic_mapping(tabs)
    except Exception as e:
        logger.error("[SchemaInference] LLM inference failed: %s", str(e))
        return _heuristic_mapping(tabs)


def _heuristic_mapping(tabs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Fallback heuristic-based schema mapping when LLM is unavailable.
    
    Uses keyword matching to guess entity types and field mappings.
    """
    mappings: list[dict[str, Any]] = []
    
    # Common header variations for each field
    field_keywords: dict[str, list[str]] = {
        # Contact fields
        "name": ["name", "full name", "contact name", "person", "fullname"],
        "email": ["email", "e-mail", "email address", "mail"],
        "phone": ["phone", "telephone", "mobile", "cell", "phone number"],
        "title": ["title", "job title", "position", "role"],
        
        # Account fields
        "domain": ["domain", "website", "url", "web"],
        "industry": ["industry", "sector", "vertical"],
        "employee_count": ["employees", "employee count", "size", "headcount", "# employees"],
        "annual_revenue": ["revenue", "annual revenue", "arr", "sales"],
        
        # Deal fields
        "amount": ["amount", "value", "deal value", "price", "deal amount", "total"],
        "stage": ["stage", "status", "deal stage", "phase", "pipeline stage"],
        "probability": ["probability", "likelihood", "win rate", "chance", "prob"],
        "close_date": ["close date", "expected close", "closing date", "close by"],
        
        # Relationship fields
        "account_name": ["company", "company name", "account", "organization", "org", "business"],
        "contact_email": ["contact email", "primary contact", "contact"],
    }
    
    for tab in tabs:
        headers_lower = [h.lower().strip() for h in tab["headers"]]
        column_mappings: dict[str, str] = {}
        ignored_columns: list[str] = []
        
        # Match headers to fields
        for i, header in enumerate(tab["headers"]):
            header_lower = header.lower().strip()
            matched = False
            
            for field, keywords in field_keywords.items():
                if any(kw in header_lower or header_lower in kw for kw in keywords):
                    column_mappings[header] = field
                    matched = True
                    break
            
            if not matched:
                ignored_columns.append(header)
        
        # Determine entity type based on mapped fields
        mapped_fields = set(column_mappings.values())
        
        entity_type = "contact"  # Default
        confidence = 0.5
        
        # Deal indicators
        deal_fields = {"amount", "stage", "probability", "close_date"}
        if deal_fields & mapped_fields:
            entity_type = "deal"
            confidence = 0.7 if len(deal_fields & mapped_fields) >= 2 else 0.5
        
        # Account indicators
        account_fields = {"domain", "industry", "employee_count", "annual_revenue"}
        if account_fields & mapped_fields and "email" not in mapped_fields:
            entity_type = "account"
            confidence = 0.7 if len(account_fields & mapped_fields) >= 2 else 0.5
        
        # Contact indicators (has email and/or name without deal/account fields)
        if "email" in mapped_fields and not (deal_fields & mapped_fields):
            entity_type = "contact"
            confidence = 0.8
        
        # For account entity, rename 'name' mapping if we found company names
        if entity_type == "deal" and "name" in column_mappings.values():
            # Check if we should use account_name instead
            for header, field in list(column_mappings.items()):
                if field == "name":
                    header_lower = header.lower()
                    if "deal" in header_lower or "opportunity" in header_lower:
                        pass  # Keep as name
                    elif "company" in header_lower or "account" in header_lower:
                        column_mappings[header] = "account_name"
        
        mappings.append({
            "tab_name": tab["tab_name"],
            "entity_type": entity_type,
            "confidence": confidence,
            "column_mappings": column_mappings,
            "ignored_columns": ignored_columns,
            "notes": "Mapped using keyword heuristics (LLM unavailable)",
        })
    
    return {"mappings": mappings}


def validate_mapping(
    entity_type: str,
    column_mappings: dict[str, str],
) -> tuple[bool, Optional[str]]:
    """
    Validate that a mapping is valid for the entity type.
    
    Args:
        entity_type: "contact", "account", or "deal"
        column_mappings: Dict of column name to field name
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if entity_type not in TARGET_SCHEMAS:
        return False, f"Invalid entity type: {entity_type}"
    
    valid_fields = set(TARGET_SCHEMAS[entity_type].keys())
    
    # Check all mapped fields are valid for this entity type
    for col_name, field_name in column_mappings.items():
        if field_name not in valid_fields:
            return False, f"Field '{field_name}' is not valid for {entity_type}"
    
    # Check required fields
    mapped_fields = set(column_mappings.values())
    
    if entity_type == "contact":
        if "email" not in mapped_fields and "name" not in mapped_fields:
            return False, "Contacts require at least 'email' or 'name' mapping"
    elif entity_type == "account":
        if "name" not in mapped_fields:
            return False, "Accounts require 'name' mapping"
    elif entity_type == "deal":
        if "name" not in mapped_fields:
            return False, "Deals require 'name' mapping"
    
    return True, None


def get_target_schemas() -> dict[str, dict[str, str]]:
    """Return the target schemas for frontend display."""
    return TARGET_SCHEMAS
