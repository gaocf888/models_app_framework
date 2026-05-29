from app.analysis_agent.slots.generic import slots_from_specs
from app.analysis_agent.slots.specs import SLOT_SPECS_BY_TYPE


def maintenance_strategy_slots():
    return slots_from_specs(SLOT_SPECS_BY_TYPE["maintenance_strategy"])
