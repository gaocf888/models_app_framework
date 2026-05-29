from app.analysis_agent.slots.generic import slots_from_specs
from app.analysis_agent.slots.specs import SLOT_SPECS_BY_TYPE


def leakage_burst_analysis_slots():
    return slots_from_specs(SLOT_SPECS_BY_TYPE["leakage_burst_analysis"])
