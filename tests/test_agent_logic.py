import sys
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
sys.path.insert(0, str(AGENTS))

from domain import (  # noqa: E402
    build_otc_order_quote,
    build_pharmacy_fulfillment_status,
    explain_prescription_document,
    explain_prescription,
    format_otc_order_preview,
    is_otc_order_intent,
    is_pharmacy_status_intent,
    otc_order_paid_result,
    orchestrate_care,
    pharmacy_monitoring_result,
    pharmacy_status_update_result,
    pharmacy_unpaid_result,
    triage_request,
)
from models import CareRequest, PrescriptionDocumentRequest  # noqa: E402
from prescription_agent import PRESCRIPTION_CONTEXT_BY_SENDER, prescription_chat_response  # noqa: E402


class AgentLogicTests(unittest.TestCase):
    def make_request(self, text: str, context=None) -> CareRequest:
        return CareRequest(
            case_id="case-test-001",
            user_id="user-test",
            text=text,
            context=context,
        )

    def test_pharmacy_fulfillment_status_has_fet_quote(self):
        request = self.make_request(
            "Is my prescription ready at CVS Westwood for delivery?",
            {
                "location": "Los Angeles, CA",
                "preference": "delivery",
                "pharmacy_name": "CVS Pharmacy - Westwood Blvd",
                "medication": "Atorvastatin",
                "dosage": "20 mg",
            },
        )
        status = build_pharmacy_fulfillment_status(request)

        self.assertEqual(status.medication, "Atorvastatin")
        self.assertEqual(status.status, "ready_for_delivery")
        self.assertEqual(status.payment_quote.currency, "FET")
        self.assertEqual(status.payment_quote.payment_method, "fet_direct")

    def test_pharmacy_monitoring_results(self):
        request = self.make_request(
            "Keep checking whether my prescription is ready at CVS Westwood for pickup",
            {
                "location": "Los Angeles, CA",
                "preference": "pickup",
                "pharmacy_name": "CVS Pharmacy - Westwood Blvd",
            },
        )
        status = build_pharmacy_fulfillment_status(request)

        paid = pharmacy_monitoring_result(request, status)
        update = pharmacy_status_update_result(
            request,
            build_pharmacy_fulfillment_status(request, monitor_tick=2),
        )
        unpaid = pharmacy_unpaid_result(request, "demo reject")

        self.assertEqual(paid.status, "monitoring")
        self.assertEqual(update.status, "ready_for_pickup")
        self.assertTrue(any("Payment completed" in event for event in paid.timeline_events))
        self.assertEqual(unpaid.status, "payment_required")
        self.assertIn("active automatic monitoring", unpaid.summary)

    def test_pharmacy_status_can_infer_hidden_pending_prescription(self):
        request = self.make_request("Is my prescription ready at CVS Westwood?")
        status = build_pharmacy_fulfillment_status(request)

        self.assertEqual(status.medication, "Metformin")
        self.assertEqual(status.dosage, "500 mg")
        self.assertEqual(status.status, "in_progress")

    def test_pharmacy_assistant_handles_otc_order_quote(self):
        request = self.make_request(
            "Find the best allergy medicine near Westwood and order it for delivery",
            {"location": "Westwood, Los Angeles, CA", "preference": "delivery"},
        )
        order = build_otc_order_quote(request)
        preview = format_otc_order_preview(order)
        paid = otc_order_paid_result(request, order)

        self.assertTrue(is_otc_order_intent(request.text))
        self.assertEqual(order.product.active_ingredient, "Loratadine")
        self.assertEqual(order.payment_quote.currency, "FET")
        self.assertIn("Other options considered", preview)
        self.assertIn("Checkout handoff", preview)
        self.assertEqual(paid.status, "order_ready_for_checkout")
        self.assertIn("https://www.costplusdrugs.com", paid.summary)

    def test_pharmacy_assistant_separates_prescription_status_intent(self):
        self.assertTrue(is_pharmacy_status_intent("Is my prescription ready at CVS Westwood?"))
        self.assertFalse(is_otc_order_intent("Is my prescription ready at CVS Westwood?"))

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

    def test_prescription_chat_answers_followup_from_last_scan(self):
        sender = "user-followup"
        PRESCRIPTION_CONTEXT_BY_SENDER.pop(sender, None)

        first_response = prescription_chat_response(
            None,
            sender,
            (
                "Prescription Medicine List\n"
                "Amoxicillin 500mg 3timesaday Oral Bacterial infections 2\n"
                "Metformin 500mg Twiceaday Oral Type 2 diabetes 3\n"
                "Albuterol 90mcg Asneeded Inhalation Asthma or COPD 3"
            ),
        )
        followup_response = prescription_chat_response(
            None,
            sender,
            "is there any order to take the medications",
        )

        self.assertIn("Amoxicillin", first_response)
        self.assertIn("Amoxicillin", followup_response)
        self.assertIn("Metformin", followup_response)
        self.assertIn("usually is not a single required order", followup_response)
        self.assertNotIn("upload", followup_response.lower())


if __name__ == "__main__":
    unittest.main()
