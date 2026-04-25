from uagents import Bureau

from adherence_agent import agent as adherence_agent
from appointment_agent import agent as appointment_agent
from caregiver_agent import agent as caregiver_agent
from orchestrator_agent import agent as orchestrator_agent
from pharmacy_agent import agent as pharmacy_agent
from prescription_agent import agent as prescription_agent
from triage_agent import agent as triage_agent


bureau = Bureau()
bureau.add(orchestrator_agent)
bureau.add(pharmacy_agent)
bureau.add(prescription_agent)
bureau.add(appointment_agent)
bureau.add(caregiver_agent)
bureau.add(triage_agent)
bureau.add(adherence_agent)


if __name__ == "__main__":
    bureau.run()
