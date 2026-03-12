from src.enumerators.error import ErrorEnumerator
from src.enumerators.type_error import TypeErrorEnumerator
from src.enumerators.flow_type_error import FlowBasedTypeErrorEnumerator
from src.enumerators.accessibility_error import AccessibilityErrorEnumerator
from src.enumerators.final_var_error import FinalVarErrorEnumerator


def get_error_enumerator(name: str) -> ErrorEnumerator:
    enumerators = {
        "type": TypeErrorEnumerator,
        "flow-type": FlowBasedTypeErrorEnumerator,
        "accessibility": AccessibilityErrorEnumerator,
        "final-var": FinalVarErrorEnumerator,
    }
    return enumerators.get(name)
