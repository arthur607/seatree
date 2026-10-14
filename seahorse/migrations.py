"""Vault changes between versions. Each step runs once, when a vault goes past its version.

A step changes only what Seahorse owns (system files, the structure, frontmatter fields), never the text of the
user's notes. Add steps at the end, oldest first:

    def _to_1_1_0() -> None:
        ...

    MIGRATIONS = [("1.1.0", _to_1_1_0)]
"""
MIGRATIONS: list = []
