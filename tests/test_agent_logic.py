import sys
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
sys.path.insert(0, str(AGENTS))

from domain import (  # noqa: E402
    build_pharmacy_recommendation,
    explain_prescription_document,
    explain_prescription,
    orchestrate_care,
    pharmacy_paid_result,
    pharmacy_unpaid_result,
    triage_request,
)
from models import CareRequest, PrescriptionDocumentRequest  # noqa: E402
from prescription_agent import prescription_chat_response  # noqa: E402


class AgentLogicTests(unittest.TestCase):
    def make_request(self, text: str, context=None) -> CareRequest:
        return CareRequest(
            case_id="case-test-001",
            user_id="user-test",
            text=text,
            context=context,
        )

    def test_pharmacy_recommendation_has_fet_quote(self):
        request = self.make_request(
            "Need delivery for atorvastatin 20mg",
            {"location": "Los Angeles, CA", "preference": "delivery"},
        )
        recommendation = build_pharmacy_recommendation(request)

        self.assertEqual(recommendation.medication, "Atorvastatin")
        self.assertEqual(recommendation.payment_quote.currency, "FET")
        self.assertEqual(recommendation.payment_quote.payment_method, "fet_direct")
        self.assertGreaterEqual(len(recommendation.options), 3)

    def test_pharmacy_payment_results(self):
        request = self.make_request("metformin 500mg pickup")
        recommendation = build_pharmacy_recommendation(request)

        paid = pharmacy_paid_result(request, recommendation)
        unpaid = pharmacy_unpaid_result(request, "demo reject")

        self.assertEqual(paid.status, "completed")
        self.assertTrue(any("Payment completed" in event for event in paid.timeline_events))
        self.assertEqual(unpaid.status, "payment_required")

    def test_triage_blocks_emergency(self):
        result = triage_request(self.make_request("My dad has chest pain and cannot breathe"))

        self.assertEqual(result.status, "urgent_escalation")
        self.assertIn("Call emergency services now.", result.next_actions)

    def test_prescription_and_orchestrator_outputs(self):
        request = self.make_request("Explain lisinopril 10mg and book a doctor")
        prescription = explain_prescription(request)
        orchestrated = orchestrate_care(request)

        self.assertEqual(prescription.status, "completed")
        self.assertIn("not medical advice", prescription.summary)
        self.assertEqual(orchestrated.agent_name, "careloop-orchestrator")
        self.assertIn("Prescription explained", orchestrated.timeline_events)

    def test_prescription_document_text_explains_actual_label_text(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-001",
            user_id="user-test",
            document_text=(
                "Rx Lisinopril 10 mg tablets. Sig: Take one tablet by mouth once daily. "
                "Qty: 30 Refills: 2 Prescriber: Dr. Maya Chen"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Lisinopril", result.summary)
        self.assertIn("once daily", result.summary)
        self.assertIn("not medical advice", result.summary)

    def test_prescription_document_handles_multiple_labels(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-002",
            user_id="user-test",
            document_text=(
                "Take 1 tablet by mouth, daily.\n"
                "Apixaban (Eliquis) .5 mg (Qty 30)\n"
                "Refills: 0\n"
                "Dr. Frank Hemorrhage\n"
                "Take 1 tablet by mouth, twice a day before meals\n"
                "Pantoprazole 20 mg (Qty 90)\n"
                "Refills: 1\n"
                "Dr. Carl Jr. Habanero"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Apixaban", result.summary)
        self.assertIn("Pantoprazole", result.summary)
        self.assertIn("twice a day before meals", result.summary)

    def test_prescription_document_handles_medicine_list_table_ocr(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-004",
            user_id="user-test",
            document_text=(
                "Prescription Medicine List\n"
                "Medication Dosage Frequency Route of Indications Refills\n"
                "Name Administration\n"
                "Amoxicillin S0Omg 3timesaday Oral Bacterial infections 2\n"
                "Metformin 500mg Twiceaday Oral Type 2 diabetes 3\n"
                "Lisinopril 20 mg Once daily Oral Hypertension 0\n"
                "Atorvastatin 10mg Once daily Oral Hyperlipidemia 1\n"
                "Albuterol 90mcg Asneeded Inhalation Asthma or COPD 3\n"
                "Inhaler exacerbation"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Amoxicillin 500 mg", result.summary)
        self.assertIn("3 times a day", result.summary)
        self.assertIn("Metformin 500 mg", result.summary)
        self.assertIn("twice a day", result.summary)
        self.assertIn("Albuterol 90 mcg", result.summary)

    def test_prescription_document_prefers_attachment_over_prompt_text(self):
        with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("Rx Lisinopril 20 mg. Sig: Take one tablet by mouth once daily.")
            path = handle.name

        try:
            request = PrescriptionDocumentRequest(
                case_id="case-rx-doc-005",
                user_id="user-test",
                document_text="please explain this image",
                document_path=path,
            )

            result = explain_prescription_document(request)

            self.assertEqual(result.status, "completed")
            self.assertIn("Lisinopril 20 mg", result.summary)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_prescription_document_rejects_binary_garbage(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-003",
            user_id="user-test",
            document_text="\x00\x01ftypavif\x00\x00random bottles 3 G daily",
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "needs_clearer_document")
        self.assertNotIn("bottles", result.summary.lower())

    def test_prescription_chat_handles_greeting_without_fake_scan(self):
        response = prescription_chat_response(None, "user-test", "hi")

        self.assertIn("prescription", response.lower())
        self.assertNotIn("Medication not confidently detected", response)
        self.assertNotIn("Status:", response)

    def test_prescription_chat_asks_for_label_when_intent_is_unclear(self):
        response = prescription_chat_response(None, "user-test", "what is your favorite color?")

        self.assertIn("upload", response.lower())
        self.assertNotIn("Medication not confidently detected", response)


if __name__ == "__main__":
    unittest.main()
