from typing import List
import itertools


def create_comparison_methods(types: List[str]) -> List[dict]:
    methods = []
    symbols = [">", "<", ">=", "<="]
    for symbol in symbols:
        for param_a, param_b in itertools.product(types, types):
            methods.append(
                {
                    "name": symbol,
                    "parameters": [
                        param_a,
                        param_b
                    ],
                    "return_type": "boolean",
                    "type_parameters": [],
                    "is_static": True,
                    "is_constructor": False,
                    "access_mod": "public",
                    "other_metadata": {
                        "symbol": symbol,
                        "is_special": True,
                    }
                })
    return methods


def create_arithmetic_methods(types: List[str], ret_type: str,
                              second_types: List[str] = None) -> List[dict]:
    methods = []
    symbols = ["+", "-", "*"]
    second_types = second_types or types
    for symbol in symbols:
        for param_a, param_b in itertools.product(types, second_types):
            methods.append(
                {
                    "name": symbol,
                    "parameters": [
                        param_a,
                        param_b
                    ],
                    "return_type": ret_type,
                    "type_parameters": [],
                    "is_static": True,
                    "is_constructor": False,
                    "access_mod": "public",
                    "other_metadata": {
                        "symbol": symbol,
                        "is_special": True,
                    }
                })
    return methods


JAVA_SPECIAL_METHODS = {
    "builtin.ops": {
        "name": "builtin.ops",
        "is_class": False,
        "language": "java",
        "type_parameters": [],
        "inherits": [],
        "implements": [],
        "fields": [],
        "methods": [
            {
                "name": "&&",
                "parameters": [
                    "boolean",
                    "boolean"
                ],
                "return_type": "boolean",
                "type_parameters": [],
                "is_static": True,
                "is_constructor": False,
                "access_mod": "public",
                "other_metadata": {
                    "symbol": "&&",
                    "is_special": True,
                }
            },
            {
                "name": "||",
                "parameters": [
                    "boolean",
                    "boolean"
                ],
                "return_type": "boolean",
                "type_parameters": [],
                "is_static": True,
                "is_constructor": False,
                "access_mod": "public",
                "other_metadata": {
                    "symbol": "||",
                    "is_special": True,
                }
            },
        ]
    }
}
JAVA_SPECIAL_METHODS["builtin.ops"]["methods"].extend(
    create_comparison_methods([
        "char",
        "byte",
        "short",
        "int",
        "long",
        "double",
        "float"
    ])
)
JAVA_SPECIAL_METHODS["builtin.ops"]["methods"].extend(
    create_arithmetic_methods([
        "byte",
        "short",
        "int",
    ], "int")
)
JAVA_SPECIAL_METHODS["builtin.ops"]["methods"].extend(
    create_arithmetic_methods([
        "byte",
        "short",
        "int",
        "float",
        "double"
    ], "java.lang.Number", second_types=["char"])
)
JAVA_SPECIAL_METHODS["builtin.ops"]["methods"].extend(
    create_arithmetic_methods(["char"], "java.lang.Number",
                              second_types=["byte", "short", "int", "float",
                                            "double"])
)
