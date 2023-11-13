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
