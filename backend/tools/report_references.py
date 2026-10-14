"""Select report references without losing targets for explicit citations."""


def visible_references(references: list[dict], limit: int) -> list[dict]:
    """Keep every labeled source; retain the legacy limit for unlabeled reports."""
    return references if any(ref.get("label") for ref in references) else references[:limit]
