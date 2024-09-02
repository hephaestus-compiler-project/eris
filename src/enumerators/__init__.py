from src.enumerators.error import ErrorEnumerator
from src.enumerators.type_error import TypeErrorEnumerator
from src.enumerators.flow_type_error import FlowBasedTypeErrorEnumerator


def get_error_enumerator(name: str) -> ErrorEnumerator:
    enumerators = {
        "type": TypeErrorEnumerator,
        "flow-type": FlowBasedTypeErrorEnumerator,
    }
    return enumerators.get(name)
