from app.analysis_agent.slots.generic import slots_from_specs
from app.analysis_agent.slots.specs import SLOT_SPECS_BY_TYPE


def four_tube_health_interpretation_slots():
    return slots_from_specs(SLOT_SPECS_BY_TYPE["four_tube_health_interpretation"])
