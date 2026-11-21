"""Single source of truth for the columns RxScan extracts.

Each field: (key, sheet header, description given to the vision model, is_text_id).
is_text_id=True means the value is written with a leading apostrophe so Google
Sheets keeps leading zeros (Rx numbers, NDCs, phone numbers).
"""

FIELDS = [
    ("date_filled", "Date Filled", "Fill/dispense or purchase date, formatted YYYY-MM-DD", False),
    ("patient_name", "Patient", "Patient name as printed", False),
    ("medication", "Medication", "Drug name (brand or generic as printed), without strength", False),
    ("strength", "Strength", "Dose strength, e.g. '10 mg', '500 mg/5 mL'", False),
    ("dosage_form", "Form", "Tablet, capsule, solution, cream, inhaler, etc.", False),
    ("quantity", "Quantity", "Quantity dispensed (number only if possible)", False),
    ("days_supply", "Days Supply", "Days supply, number only", False),
    ("refills_remaining", "Refills Left", "Refills remaining, number only", False),
    ("rx_number", "Rx Number", "Prescription/Rx number", True),
    ("ndc", "NDC", "National Drug Code if printed", True),
    ("prescriber", "Prescriber", "Prescribing doctor/provider name", False),
    ("pharmacy_name", "Pharmacy", "Pharmacy name and store number if shown", False),
    ("pharmacy_address", "Pharmacy Address", "Pharmacy street address, city, state", False),
    ("pharmacy_phone", "Pharmacy Phone", "Pharmacy phone number", True),
    ("insurance_plan", "Insurance", "Insurance plan/carrier name if shown", False),
    ("retail_price", "Retail Price", "Full/retail/usual & customary price before insurance, number only", False),
    ("insurance_paid", "Insurance Paid", "Amount paid by insurance/plan, number only", False),
    ("savings", "Savings", "Savings/discount amount shown, number only", False),
    ("patient_paid", "You Paid", "Amount the patient paid / copay for THIS prescription, number only", False),
    ("payment_method", "Payment Method", "Card type/last 4 or cash, as printed (never a full card number)", False),
    ("transaction_id", "Transaction ID", "Receipt/transaction/reference number", True),
    ("notes", "Notes", "Anything else notable or unclear (e.g. 'total split across 2 Rx')", False),
]

# Columns added by the app, not the model.
META_FIELDS = [
    ("receipt_url", "Receipt", False),
    ("source_file", "Source Image", True),
    ("added_at", "Added At", False),
]

ALL_HEADERS = [f[1] for f in FIELDS] + [m[1] for m in META_FIELDS]
ALL_KEYS = [f[0] for f in FIELDS] + [m[0] for m in META_FIELDS]
TEXT_ID_KEYS = {f[0] for f in FIELDS if f[3]} | {m[0] for m in META_FIELDS if m[2]}
KEY_TO_HEADER = dict(zip(ALL_KEYS, ALL_HEADERS))


def json_schema() -> dict:
    """JSON schema for the model's structured output."""
    props = {
        key: {"type": ["string", "null"], "description": desc}
        for key, _h, desc, _t in FIELDS
    }
    return {
        "type": "object",
        "properties": {
            "prescriptions": {
                "type": "array",
                "description": "One entry per prescription line item on the receipt",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": [f[0] for f in FIELDS],
                },
            },
            "is_prescription_receipt": {
                "type": "boolean",
                "description": "False if the image is not a pharmacy/prescription receipt or label",
            },
        },
        "required": ["prescriptions", "is_prescription_receipt"],
    }


def gemini_schema() -> dict:
    """Same schema in the OpenAPI subset Gemini's responseSchema accepts."""
    props = {
        key: {"type": "STRING", "nullable": True, "description": desc}
        for key, _h, desc, _t in FIELDS
    }
    return {
        "type": "OBJECT",
        "properties": {
            "is_prescription_receipt": {"type": "BOOLEAN"},
            "prescriptions": {
                "type": "ARRAY",
                "items": {"type": "OBJECT", "properties": props, "propertyOrdering": [f[0] for f in FIELDS]},
            },
        },
        "required": ["is_prescription_receipt", "prescriptions"],
        "propertyOrdering": ["is_prescription_receipt", "prescriptions"],
    }
