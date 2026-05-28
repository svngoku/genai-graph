
// -------------------------
// ENUMS (Taxonomies)
// -------------------------

enum ProjectType {
  IT_SERVICES          @description("IT services or IT operations scope")
  DATA_ANALYTICS       @description("Data platform, BI, analytics, data engineering")
  CLOUD                @description("Cloud migration, cloud engineering, cloud ops")
  CYBERSECURITY        @description("Security services, SOC, IAM, compliance security")
  APPLICATION_DEV      @description("Application development or modernization")
  INFRASTRUCTURE       @description("Infrastructure services (network, compute, workplace)")
  AI_ML                @description("AI/ML, GenAI, model development or MLOps")
  MANAGED_SERVICES     @description("Managed service delivery with SLAs/operations")
  CONSULTING           @description("Consulting, advisory, assessment, roadmap")
  TRANSFORMATION       @description("Business/IT transformation program")
  INDUSTRIAL_ENGINEERING @description("Engineering services for industrial contexts")
  OTHER                @description("Other or unclear category")
  @@description("High-level project category inferred from the RFQ")
}

enum RequirementType {
  FUNCTIONAL       @description("Functional business requirement")
  NON_FUNCTIONAL   @description("Non-functional requirement (perf, availability, etc.)")
  SECURITY         @description("Security controls or security obligations")
  COMPLIANCE       @description("Compliance standard or regulatory requirement")
  ARCHITECTURE     @description("Architecture / design constraints or expectations")
  DELIVERY         @description("Delivery approach, milestones, governance, staffing")
  SUPPORT          @description("Run/support requirements (L1-L3, hours, tooling)")
  PRICING          @description("Pricing, rates, cost breakdown, commercial model")
  LEGAL_CONTRACTUAL @description("Contract terms, legal clauses, liability, IP, etc.")
  @@description("Requirement classification for routing and triage")
}

enum Priority {
  MUST      @description("Mandatory / must-have requirement")
  SHOULD    @description("Strong preference / should-have requirement")
  COULD     @description("Optional / nice-to-have requirement")
  UNKNOWN   @description("Priority not stated or unclear")
  @@description("Requirement priority as stated or implied in the RFQ")
}

enum RiskLevel {
  LOW       @description("Low impact / low likelihood")
  MEDIUM    @description("Moderate risk requiring attention")
  HIGH      @description("High risk requiring mitigation and escalation")
  CRITICAL  @description("Critical risk likely to block bid or delivery")
  @@description("Severity level used for triage and escalation")
}

enum ContractArea {
  PRICING_PAYMENT            @description("Pricing, invoicing, payment terms")
  SOW_SCOPE                  @description("Scope of work definition and boundaries")
  ACCEPTANCE                 @description("Acceptance criteria, testing, sign-off")
  SLA_SERVICE_LEVELS         @description("SLAs, KPIs, performance commitments")
  PENALTIES_SERVICE_CREDITS  @description("Penalties, credits, liquidated damages")
  LIABILITY_LIMITATION       @description("Caps, exclusions, limitation of liability")
  INDEMNITY                  @description("Indemnities and defense obligations")
  IP_OWNERSHIP_LICENSE       @description("IP ownership, licensing, deliverable rights")
  CONFIDENTIALITY            @description("Confidentiality obligations and exceptions")
  DATA_PROTECTION            @description("Data privacy, GDPR, DPA requirements")
  SECURITY_REQUIREMENTS      @description("Security clauses, audits, controls")
  SUBCONTRACTING             @description("Subcontractor rules, approvals, flow-down")
  INSURANCE                  @description("Insurance coverage and certificates")
  AUDIT_RIGHTS               @description("Audit rights, compliance verification")
  TERMINATION                @description("Termination rights, exit obligations")
  CHANGE_CONTROL             @description("Change process, scope and pricing changes")
  GOVERNING_LAW_JURISDICTION @description("Governing law, jurisdiction, disputes")
  EXPORT_CONTROLS_SANCTIONS  @description("Export control, sanctions, restricted parties")
  ETHICS_COMPLIANCE          @description("Ethics, anti-bribery, code of conduct")
  OTHER                      @description("Other contractual topic")
  @@description("Contract clause buckets to structure legal/commercial review")
}

enum SubmissionMode {
  PORTAL   @description("Submission via procurement portal")
  EMAIL    @description("Submission via email")
  BOTH     @description("Both portal and email required/allowed")
  UNKNOWN  @description("Submission method not clearly stated")
  @@description("How the response must be submitted")
}

// -------------------------
// REUSABLE TYPES
// -------------------------

class Evidence {
  quote string @description("Short verbatim excerpt supporting an extracted fact")
  location_hint string? @description("Where the quote appears (page/section/appendix) if known")
  @@description("Traceability object linking extracted data to RFQ text evidence")
}

class Requirement {
  id string? @description("Requirement ID from the RFQ (e.g., M1, R9) if present")
  title string @description("Short requirement label")
  type RequirementType @description("Requirement category for routing and review")
  priority Priority @description("MUST/SHOULD/COULD based on RFQ language")
  description string @description("Requirement description close to RFQ wording")
  acceptance_criteria string? @description("Explicit acceptance criteria, tests, KPIs if stated")
  dependencies string[]? @description("Prerequisites or dependencies (systems, inputs, third parties)")
  evidence Evidence[]? @description("1–3 quotes supporting this requirement")
  @@description("Single requirement extracted from the RFQ")
}

class ContractualCondition {
  area ContractArea @description("Contract clause category")
  summary string @description("Plain-English summary of the clause")
  key_terms string[]? @description("Extracted key terms (numbers, thresholds, deadlines) if any")
  red_flag bool @description("True if clause is a potential blocker or needs escalation")
  risk_level RiskLevel @description("Severity level for this clause")
  negotiation_notes string? @description("Negotiation posture or clarification needs")
  evidence Evidence[]? @description("Quotes supporting the clause extraction")
  @@description("Contractual/commercial clause extracted for triage")
}

class Milestone {
  name string @description("Milestone name (questions due, proposal due, award, kickoff, go-live)")
  date string? @description("Date as written in the RFQ (do not normalize if ambiguous)")
  relative_timing string? @description("Relative timing as written (e.g., T0+6 weeks)")
  notes string? @description("Additional context (dependencies, time windows, constraints)")
  evidence Evidence[]? @description("Quotes supporting the milestone")
  @@description("Timeline milestone extracted from the RFQ")
}

class Stakeholder {
  name_or_role string @description("Named person or role (e.g., Procurement, Legal, Security)")
  organization string? @description("Organization/department if mentioned")
  contact string? @description("Contact details (email/portal/contact form) if provided")
  evidence Evidence[]? @description("Quotes supporting the stakeholder identification")
  @@description("Stakeholder/contact point extracted from the RFQ")
}

class EvaluationCriterion {
  name string @description("Criterion label (technical, commercial, references, etc.)")
  weight_or_points string? @description("Weight/points if stated (e.g., 40%, max 80 points)")
  description string? @description("How it is evaluated (matrix, demos, interviews, etc.)")
  evidence Evidence[]? @description("Quotes supporting scoring/weights")
  @@description("Evaluation/scoring criterion extracted from the RFQ")
}

class Deliverable {
  name string @description("Deliverable name (design, runbook, report, etc.)")
  description string? @description("Deliverable scope/definition as stated")
  frequency string? @description("Frequency if recurring (weekly, monthly, on-demand)")
  evidence Evidence[]? @description("Quotes supporting deliverable extraction")
  @@description("Expected deliverable extracted from the RFQ")
}

class PricingItem {
  pricing_model string? @description("Pricing model requested (fixed price, T&M, unit rates, etc.)")
  currency string? @description("Currency if specified")
  required_breakdown string[]? @description("Mandatory breakdown items (rates, licenses, travel, etc.)")
  constraints string[]? @description("Pricing constraints (e.g., pricing not required at RFQ stage)")
  evidence Evidence[]? @description("Quotes supporting pricing instructions")
  @@description("Pricing and commercial submission instructions")
}

class ComplianceItem {
  topic string @description("Compliance topic (e.g., ISO 27001, PCI DSS, GDPR)")
  requirement string @description("What is required (certification, controls, reporting, audit)")
  mandatory bool @description("True if explicitly mandatory, otherwise false")
  evidence Evidence[]? @description("Quotes supporting the compliance requirement")
  @@description("Compliance/security requirement extracted from the RFQ")
}

class RiskItem {
  title string @description("Short risk label (e.g., unlimited liability, aggressive timeline)")
  description string @description("Why it is a risk and what it affects")
  risk_level RiskLevel @description("Risk severity for triage")
  impact string? @description("Impact if stated or clearly implied by RFQ terms")
  mitigation_idea string? @description("High-level mitigation recommendation (do not invent facts)")
  evidence Evidence[]? @description("Quotes supporting the risk")
  @@description("Top risk item for triage decision-making")
}

class MissingInfoQuestion {
  question string @description("Clarification question to ask the customer/procurement")
  why_it_matters string? @description("Why this affects feasibility, price, risk, or compliance")
  related_section string? @description("Related RFQ section if the gap is localized")
  @@description("Missing information to clarify before committing to bid")
}

class SubmissionInstructions {
  mode SubmissionMode @description("Submission channel (portal/email/both/unknown)")
  portal_or_email_details string? @description("Portal URL or email instructions as written")
  format_requirements string[]? @description("Format constraints (templates, PDF/Word, max pages, naming)")
  questions_process string? @description("How Q&A/clarifications must be submitted and answered")
  due_dates_summary string? @description("Short sentence summarizing key due dates")
  @@description("How and what to submit in response to the RFQ")
}

// -------------------------
// MAIN OUTPUT STRUCTURE
// -------------------------

class RFQAnalysis {
  rfq_title string? @description("RFQ title or subject line")
  rfq_reference_number string? @description("RFQ/tender reference number if provided")
  issuing_organization string? @description("Issuing organization or customer entity")
  confidentiality_markings string[]? @description("Markings like Confidential, Export Controls, etc.")

  project_type ProjectType @description("Best-fit project category inferred from the RFQ")
  project_type_rationale string? @description("Short justification for the chosen project type")

  executive_summary string @description("8–12 lines summarizing what is requested and why")
  in_scope string[]? @description("Explicit in-scope items (services, systems, geographies)")
  out_of_scope string[]? @description("Explicit exclusions stated in the RFQ")
  assumptions string[]? @description("Assumptions stated in RFQ or assumptions to validate")
  constraints string[]? @description("Constraints (tools, locations, on-site, languages, etc.)")

  main_requirements Requirement[] @description("Key requirements, prioritizing MUST items")
  non_functional_summary string? @description("Summary of NFRs (availability, performance, etc.)")
  integrations_and_dependencies string[]? @description("Integration points and external dependencies")
  deliverables Deliverable[]? @description("Expected deliverables and reporting artifacts")

  contractual_conditions ContractualCondition[] @description("Key contractual/commercial clauses and risks")
  pricing PricingItem? @description("Pricing model and instructions if present")
  key_commercial_notes string? @description("Short commercial summary and constraints")

  timeline Milestone[] @description("All key deadlines and delivery milestones")
  submission_instructions SubmissionInstructions @description("Submission channel, format, Q&A process")

  evaluation_criteria EvaluationCriterion[] @description("Scoring criteria with weights/points if stated")
  mandatory_gate_criteria string[]? @description("Pass/fail mandatory criteria or compliance gates")
  decision_process_notes string? @description("Selection process notes (stages, demos, BAFO, etc.)")

  stakeholders Stakeholder[] @description("Named roles/contacts relevant to the RFQ process")
  compliance_requirements ComplianceItem[] @description("Compliance/security obligations to satisfy")

  top_risks RiskItem[] @description("Top risks with severity and evidence")
  red_flags string[] @description("One-line red flags for fast go/no-go review")

  missing_information_questions MissingInfoQuestion[] @description("Questions to clarify before bidding")
 
  key_evidence Evidence[]? @description("Extra headline quotes supporting the most important points")

  @@description("Structured RFQ triage output with evidence, risks, gaps, and recommendation")
}

// -------------------------
// INPUT + FUNCTION
// -------------------------


 
function AnalyzeRFQ(input: string) -> RFQAnalysis {
  client "GptOss120" 
  prompt #"
You are a meticulous RFQ analyst. Extract a first-pass assessment from the RFQ text.
Return a RFQAnalysis object.

Non-negotiable rules:
- Do NOT invent facts. If unknown/unclear, set null/UNKNOWN and add a MissingInfoQuestion.
- Prefer concise, structured summaries.
- For each critical requirement or contractual condition, attach 1-3 Evidence quotes (verbatim).
- When you can, include section/page hints in Evidence.location_hint.
- Identify project_type using the ProjectType taxonomy and justify briefly.
- Highlight contractual red flags (red_flag=true) and assign risk_level.
- Build a timeline with all dates/deadlines mentioned (questions due, submission due, award, kickoff, delivery milestones).
- Capture evaluation criteria, weights/points, and mandatory gate criteria (pass/fail).
- Provide a triage go/no-go recommendation: GO / NO_GO / CONDITIONAL_GO / UNKNOWN with rationale.
- Set confidence_0_to_1 based on how complete and explicit the RFQ text is.

RFQ text:
{{ input }}


{{ ctx.output_format }}
"#
}

