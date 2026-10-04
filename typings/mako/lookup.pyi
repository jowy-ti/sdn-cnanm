from collections.abc import Sequence


class TemplateLookup:
    def __init__(self, directories: Sequence[str], **options: object) -> None: ...
