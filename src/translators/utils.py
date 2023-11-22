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
        cls = lib_spec[cls_name]
        return cls["class_type"]
    return cls.class_type


def strip_fqn(func):
    def inner(self, *args, **kwargs):
        res = func(self, *args, **kwargs)
        t = args[0]
        type_name = t.name
        if "." in type_name:
            type_name = type_name.rsplit(".", 1)[1]

        # Strip fqn if the class is defined in context.
        context = self.context or Context()
        defined_classes = context.get_classes(self._namespace, glob=True)
        if type_name in defined_classes:
            return res.replace(t.name, type_name)
        return res
    return inner
