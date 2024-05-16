import re

from src.ir.context import Context


def get_modifier_list(metadata: dict) -> list:
    modifiers = []
    for k, v in metadata.items():
        if not v:
            continue
        if v is True:
            modifiers.append(k)
        else:
            modifiers.append(v)
    return modifiers


def get_class_type_from_context(cls_name: str, context: Context,
                                namespace: tuple,
                                lib_spec: dict):
    defined_classes = context.get_classes(namespace, glob=True)
    cls = defined_classes.get(cls_name)
    if cls is None:
        cls = lib_spec.get(cls_name)
        if cls is None:
            # Class specification not found in the given lib spec.
            return None
        return cls["class_type"]
    return cls.class_type


def is_parent_interface(child_name: str, parent_name: str,
                        lib_spec: dict) -> bool:
    assert child_name in lib_spec, "Child class specification not found"
    cls_spec = lib_spec[child_name]
    return not any(sc.startswith(parent_name)
                   for sc in cls_spec["inherits"])


def strip_fqn(func):
    def inner(self, *args, **kwargs):
        res = func(self, *args, **kwargs)
        t = args[0]
        type_name = t.name
        if "." in type_name:
            type_name = type_name.rsplit(".", 1)[1]

        # Strip fqn if the class is defined in context or is a type variable.
        context = self.context or Context()
        defined_classes = context.get_classes(self._namespace, glob=True)
        if type_name in defined_classes or t.is_type_var():
            type_var_cycle = (
                t.is_type_var() and
                t.bound and
                t.bound.is_type_var() and
                type_name == t.bound.name.rsplit(".", 1)[1]
            )
            if type_var_cycle:
                # Here, we handle cycles in type variables, e.g.,
                # T1 extends T1
                type_name = "F" + type_name[1:]

            return res.replace(t.name, type_name)
        return res
    return inner


def package_consistency(func):
    def inner(self, *args, **kwargs):
        res = func(self, *args, **kwargs)
        if self.package:
            package_prefix = self.package.split(".", 1)[0]
            new = re.sub(package_prefix + r"\.[a-z]+", self.package, res)
            return new
        return res
    return inner
