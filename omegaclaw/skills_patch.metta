;; CareLoop OmegaClaw Agentverse skill patch.
;;
;; Add this to the official OmegaClaw-Core `src/skills.metta` file.

;; 1. Add near the other Python skill bridge functions:
(= (careloop-healthcare $request)
   (py-call (agentverse.careloop careloop_healthcare_request $request)))

;; 2. Add this line inside `getSkills`, under the AGENTVERSE AGENTS section:
;; "- Coordinate elderly healthcare tasks through CareLoop Agentverse. Use this first for doctor booking, cough/fever care, prescriptions, OTC pharmacy, caregiver messages, reminders, or healthcare routing. Do not ask follow-up questions before using it unless the user explicitly asks: careloop-healthcare string"
