from src.enumerators.error import ErrorEnumerator
from src.enumerators.type_error import TypeErrorEnumerator


def get_error_enumerator(name: str) -> ErrorEnumerator:
    enumerators = {
        "type": TypeErrorEnumerator
    }
    return enumerators.get(name)
