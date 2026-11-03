"""
Unit tests for Milestone 4B.1 Pydantic extraction schemas.

Verifies:
1. ExtractedFactCandidate validation, fact type constraint, empty string rejections, and extra field forbidding.
2. RequirementCandidate validation, modality handling, metadata optionality, and extra field forbidding.
3. StructuredExtractionResponse combined handling, empty states, and malformed inputs.
4. Faithful preservation of Malayalam script and multi-byte Unicode strings during validation and serialization.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.enums import FactType
from app.schemas.extraction import (
    ExtractedFactCandidate,
    RequirementCandidate,
    RequirementMetadata,
    RequirementModality,
    StructuredExtractionResponse,
)

# ── 1. Fact Schema Tests ──────────────────────────────────────────────────────


def test_valid_date_fact():
    """Verify valid DATE fact parsing and normalized value preservation."""
    candidate = ExtractedFactCandidate(
        fact_type=FactType.DATE,
        raw_value="01 November 2026",
        source_quote="The circular was issued on 01 November 2026 by the Director.",
        normalized_value={"iso_date": "2026-11-01"},
        description="Circular issuance date",
    )
    assert candidate.fact_type == FactType.DATE
    assert candidate.raw_value == "01 November 2026"
    assert candidate.source_quote == "The circular was issued on 01 November 2026 by the Director."
    assert candidate.normalized_value == {"iso_date": "2026-11-01"}
    assert candidate.description == "Circular issuance date"


def test_valid_amount_fact():
    """Verify valid AMOUNT fact parsing with numeric normalized value."""
    candidate = ExtractedFactCandidate(
        fact_type="AMOUNT",
        raw_value="₹25,00,000",
        source_quote="Sanction is accorded for an expenditure not exceeding ₹25,00,000.",
        normalized_value={"amount": 2500000, "currency": "INR"},
    )
    assert candidate.fact_type == FactType.AMOUNT
    assert candidate.raw_value == "₹25,00,000"
    assert candidate.normalized_value["amount"] == 2500000


def test_valid_deadline_fact():
    """Verify valid DEADLINE fact parsing."""
    candidate = ExtractedFactCandidate(
        fact_type=FactType.DEADLINE,
        raw_value="30 November 2026",
        source_quote="The last date for submission of monthly returns is 30 November 2026.",
        normalized_value={"deadline": "2026-11-30"},
    )
    assert candidate.fact_type == FactType.DEADLINE
    assert candidate.raw_value == "30 November 2026"


def test_valid_responsible_department_fact():
    """Verify valid RESPONSIBLE_DEPARTMENT fact parsing."""
    candidate = ExtractedFactCandidate(
        fact_type=FactType.RESPONSIBLE_DEPARTMENT,
        raw_value="Operations Division",
        source_quote="Department: Operations Division has been assigned overall execution.",
        normalized_value={"department_code": "OPS-01"},
    )
    assert candidate.fact_type == FactType.RESPONSIBLE_DEPARTMENT
    assert candidate.raw_value == "Operations Division"


def test_unsupported_fact_type_rejected():
    """Verify unsupported fact types (e.g. UNKNOWN, PERSON, RANDOM) are rejected with ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        ExtractedFactCandidate(
            fact_type="UNSUPPORTED_TYPE",  # Not in FactType enum
            raw_value="Sample Value",
            source_quote="Sample Quote",
        )
    assert "fact_type" in str(exc_info.value)


def test_empty_raw_value_rejected():
    """Verify empty or whitespace-only raw_value is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ExtractedFactCandidate(
            fact_type=FactType.DATE,
            raw_value="   ",
            source_quote="Valid source quote here.",
        )
    assert "raw_value" in str(exc_info.value)


def test_empty_evidence_quote_rejected():
    """Verify empty or whitespace-only source_quote is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ExtractedFactCandidate(
            fact_type=FactType.DATE,
            raw_value="01 Nov 2026",
            source_quote="",
        )
    assert "source_quote" in str(exc_info.value)


def test_unexpected_additional_fields_rejected_in_fact():
    """Verify unexpected additional fields (like model-proposed document_id or status) are forbidden."""
    with pytest.raises(ValidationError) as exc_info:
        ExtractedFactCandidate(
            fact_type=FactType.DATE,
            raw_value="01 Nov 2026",
            source_quote="Valid quote.",
            document_id="some-id",  # Forbidden extra field
        )
    assert "Extra inputs are not permitted" in str(exc_info.value)


# ── 2. Requirement Schema Tests ───────────────────────────────────────────────


def test_valid_obligation_with_modality_must():
    """Verify valid obligation requirement with modality MUST."""
    candidate = RequirementCandidate(
        title="Submit Monthly Progress Report",
        description="All departments must submit their operational progress reports on time.",
        requirement_type="OBLIGATION",
        source_quote="All departments must submit their operational progress reports before 30 November 2026.",
        normalized_value=RequirementMetadata(
            responsible_actor="All departments",
            required_action="Submit operational progress reports",
            modality=RequirementModality.MUST,
            deadline="2026-11-30",
        ),
    )
    assert candidate.title == "Submit Monthly Progress Report"
    assert candidate.requirement_type == "OBLIGATION"
    assert candidate.normalized_value is not None
    assert candidate.normalized_value.modality == RequirementModality.MUST
    assert candidate.normalized_value.modality == "MUST"
    assert candidate.normalized_value.deadline == "2026-11-30"


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        (None, None),
        (["upon prior approval"], ["upon prior approval"]),
        (
            ["മുൻകൂർ അനുമതി ലഭിക്കുമ്പോൾ", "ഫണ്ട് ലഭ്യമാകുമ്പോൾ"],
            ["മുൻകൂർ അനുമതി ലഭിക്കുമ്പോൾ", "ഫണ്ട് ലഭ്യമാകുമ്പോൾ"],
        ),
    ],
)
def test_conditions_accept_null_one_item_and_multi_item_original_language_lists(conditions, expected):
    """Conditions remain null or a list of untouched source-language strings."""
    metadata = RequirementMetadata(conditions=conditions)

    assert metadata.conditions == expected


@pytest.mark.parametrize(
    "conditions",
    [
        "upon prior approval",
        {"trigger": "upon prior approval"},
        1,
        True,
    ],
)
def test_conditions_reject_bare_string_object_and_scalars(conditions):
    """Strict validation must not coerce non-list model output into conditions."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementMetadata(conditions=conditions)

    assert "conditions" in str(exc_info.value)


def test_valid_requirement_with_deadline():
    """Verify valid requirement with an explicit deadline."""
    candidate = RequirementCandidate(
        title="Submit expenditure claims",
        description="Expenditure statements must be reconciled and submitted before fiscal deadline.",
        requirement_type="OBLIGATION",
        source_quote="Claims must be finalized by 15 December 2026.",
        normalized_value=RequirementMetadata(
            deadline="2026-12-15",
            modality="MUST",
        ),
    )
    assert candidate.normalized_value is not None
    assert candidate.normalized_value.deadline == "2026-12-15"


def test_valid_requirement_with_no_deadline():
    """Verify valid requirement without a deadline (standing instruction or general policy)."""
    candidate = RequirementCandidate(
        title="Maintain Visitor Access Log",
        description="Security personnel shall maintain a visitor entry log at all premises.",
        requirement_type="DIRECTIVE",
        source_quote="A visitor log shall be maintained continuously at reception.",
        normalized_value=RequirementMetadata(
            responsible_actor="Security personnel",
            required_action="Maintain visitor entry log",
            modality=RequirementModality.SHOULD,
            deadline=None,
        ),
    )
    assert candidate.normalized_value is not None
    assert candidate.normalized_value.deadline is None
    assert candidate.normalized_value.modality == RequirementModality.SHOULD


def test_valid_requirement_containing_original_malayalam_text():
    """Verify requirement containing original Malayalam script preserves all characters and combining marks."""
    malayalam_title = "പ്രതിമാസ റിപ്പോർട്ട് സമർപ്പിക്കൽ"
    malayalam_desc = "എല്ലാ വകുപ്പുകളും അവരുടെ പ്രവർത്തന റിപ്പോർട്ടുകൾ ഈ തീയതിക്ക് മുമ്പായി സമർപ്പിക്കണം."
    malayalam_quote = "പ്രതിമാസ റിപ്പോർട്ട് സമർപ്പിക്കേണ്ട അവസാന തീയതി 30 നവംബർ 2026 ആണ്. എല്ലാ വകുപ്പുകളും അവരുടെ പ്രവർത്തന റിപ്പോർട്ടുകൾ ഈ തീയതിക്ക് മുമ്പായി സമർപ്പിക്കണം."

    candidate = RequirementCandidate(
        title=malayalam_title,
        description=malayalam_desc,
        requirement_type="OBLIGATION",
        source_quote=malayalam_quote,
        normalized_value=RequirementMetadata(
            responsible_actor="എല്ലാ വകുപ്പുകളും",
            required_action="പ്രവർത്തന റിപ്പോർട്ടുകൾ സമർപ്പിക്കണം",
            modality="MUST",
            deadline="30 നവംബർ 2026",
            original_language="ml",
            original_language_text=malayalam_desc,
        ),
    )
    assert candidate.title == malayalam_title
    assert candidate.description == malayalam_desc
    assert candidate.source_quote == malayalam_quote
    assert candidate.normalized_value is not None
    assert candidate.normalized_value.responsible_actor == "എല്ലാ വകുപ്പുകളും"
    assert candidate.normalized_value.original_language == "ml"


def test_empty_title_rejected():
    """Verify empty or whitespace-only title is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementCandidate(
            title="   ",
            description="Valid description.",
            requirement_type="OBLIGATION",
            source_quote="Valid source quote.",
        )
    assert "title" in str(exc_info.value)


def test_empty_evidence_quote_rejected_in_requirement():
    """Verify empty or whitespace-only source_quote is rejected on RequirementCandidate."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementCandidate(
            title="Valid Title",
            description="Valid description.",
            requirement_type="OBLIGATION",
            source_quote="",
        )
    assert "source_quote" in str(exc_info.value)


def test_unexpected_additional_fields_rejected_in_requirement():
    """Verify unexpected fields (e.g. status, verification_status, workspace_id) are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementCandidate(
            title="Valid Title",
            description="Valid description.",
            requirement_type="OBLIGATION",
            source_quote="Valid quote.",
            status="ACTIVE",  # Model cannot assign database status
        )
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_unsupported_modality_rejected():
    """Verify arbitrary unsupported modality strings are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementMetadata(modality="ARBITRARY_MODALITY")
    assert "modality" in str(exc_info.value)


def test_unsupported_requirement_type_rejected():
    """Verify arbitrary unsupported requirement types are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        RequirementCandidate(
            title="Valid Title",
            description="Valid Description",
            requirement_type="UNSUPPORTED_REQ_TYPE",
            source_quote="Valid quote",
        )
    assert "requirement_type" in str(exc_info.value)


def test_optional_structured_metadata_may_be_absent():
    """Verify normalized_value may be None or completely omitted."""
    candidate = RequirementCandidate(
        title="Standing Safety Rule",
        description="All personnel must wear badges inside the building.",
        requirement_type="DIRECTIVE",
        source_quote="Badges must be worn at all times.",
    )
    assert candidate.normalized_value is None


# ── 3. Combined Response Tests ────────────────────────────────────────────────


def test_response_containing_facts_and_requirements():
    """Verify combined extraction response containing both facts and requirements."""
    resp = StructuredExtractionResponse(
        facts=[
            ExtractedFactCandidate(
                fact_type=FactType.DATE,
                raw_value="01 November 2026",
                source_quote="Dated 01 November 2026.",
            ),
            ExtractedFactCandidate(
                fact_type=FactType.AMOUNT,
                raw_value="₹5,00,000",
                source_quote="Amount sanctioned: ₹5,00,000.",
            ),
        ],
        requirements=[
            RequirementCandidate(
                title="Submit Audit Report",
                description="Audit report must be furnished by end of month.",
                requirement_type="REPORTING",
                source_quote="Audit report must be furnished before 30 November 2026.",
            )
        ],
    )
    assert len(resp.facts) == 2
    assert len(resp.requirements) == 1
    dump = resp.model_dump()
    assert dump["facts"][0]["fact_type"] == "DATE"
    assert dump["requirements"][0]["requirement_type"] == "REPORTING"


def test_response_containing_facts_only():
    """Verify valid response when document contains only descriptive facts."""
    resp = StructuredExtractionResponse(
        facts=[
            ExtractedFactCandidate(
                fact_type=FactType.IDENTIFIER,
                raw_value="CIR/2026/003",
                source_quote="Circular reference number: CIR/2026/003.",
            )
        ],
        requirements=[],
    )
    assert len(resp.facts) == 1
    assert len(resp.requirements) == 0


def test_response_containing_requirements_only():
    """Verify valid response when document contains only requirements."""
    resp = StructuredExtractionResponse(
        facts=[],
        requirements=[
            RequirementCandidate(
                title="Mandatory Password Update",
                description="Users must change passwords every 90 days.",
                requirement_type="OBLIGATION",
                source_quote="Passwords must be updated every 90 days without exception.",
            )
        ],
    )
    assert len(resp.facts) == 0
    assert len(resp.requirements) == 1


def test_valid_empty_extraction_response():
    """Verify empty extraction response is valid (default factories populate empty lists)."""
    resp = StructuredExtractionResponse()
    assert resp.facts == []
    assert resp.requirements == []
    assert resp.model_dump() == {"facts": [], "requirements": []}


def test_malayalam_values_and_quotes_preserved_after_serialization():
    """
    Verify Malayalam values, quotes, and combining marks remain completely intact
    after validation and JSON serialization.
    """
    raw_val = "30 നവംബർ 2026"
    quote_text = "പ്രതിമാസ റിപ്പോർട്ട് സമർപ്പിക്കേണ്ട അവസാന തീയതി 30 നവംബർ 2026 ആണ്."

    resp = StructuredExtractionResponse(
        facts=[
            ExtractedFactCandidate(
                fact_type=FactType.DEADLINE,
                raw_value=raw_val,
                source_quote=quote_text,
                normalized_value={"iso_date": "2026-11-30"},
            )
        ],
        requirements=[
            RequirementCandidate(
                title="റിപ്പോർട്ട് സമർപ്പിക്കൽ",
                description="എല്ലാ വകുപ്പുകളും പ്രവർത്തന റിപ്പോർട്ട് സമർപ്പിക്കണം.",
                requirement_type="OBLIGATION",
                source_quote=quote_text,
                normalized_value=RequirementMetadata(
                    responsible_actor="എല്ലാ വകുപ്പുകളും",
                    modality="MUST",
                    original_language="ml",
                ),
            )
        ],
    )

    # Test dictionary dump
    dump = resp.model_dump()
    assert dump["facts"][0]["raw_value"] == raw_val
    assert dump["facts"][0]["source_quote"] == quote_text
    assert dump["requirements"][0]["title"] == "റിപ്പോർട്ട് സമർപ്പിക്കൽ"
    assert dump["requirements"][0]["normalized_value"]["responsible_actor"] == "എല്ലാ വകുപ്പുകളും"

    # Test JSON dump & round-trip
    json_str = resp.model_dump_json()
    assert raw_val in json_str
    assert quote_text in json_str

    reconstructed = StructuredExtractionResponse.model_validate_json(json_str)
    assert reconstructed.facts[0].raw_value == raw_val
    assert reconstructed.facts[0].source_quote == quote_text
    assert reconstructed.requirements[0].title == "റിപ്പോർട്ട് സമർപ്പിക്കൽ"


def test_malformed_field_types_rejected():
    """Verify malformed field types (e.g. facts not a list, raw_value non-string) are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        StructuredExtractionResponse.model_validate({"facts": "not_a_list"})
    assert "facts" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        StructuredExtractionResponse.model_validate({"requirements": "not_a_list"})
    assert "requirements" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ExtractedFactCandidate.model_validate(
            {
                "fact_type": "DATE",
                "raw_value": None,  # Should be string
                "source_quote": "Valid quote",
            }
        )
    assert "raw_value" in str(exc_info.value)
