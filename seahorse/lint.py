"""Checks the vault against the rules of `_AGENTS.md` and `_Vocabulary.md`.

Usage: python -m seahorse.lint [vault path]
Exit code 1 when there are errors. Warnings do not change the exit code.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SYSTEM = "6. ⚙️ System"
ENGRAMS = "5. 💭 Engrams"
AREAS_WITH_TYPE = ["1. 🎯 Intention", "2. 📓 Memory", "3. 📚 Knowledge", "4. 🪞 Identity", ENGRAMS]
RELATIONS = {"part_of", "causes", "inspires", "contradicts", "supersedes", "related"}
MAX_LINES = 200


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """A small parser: `key: value` and inline lists `[a, b]`. Enough for this vault."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    block, body = text[4:end], text[end + 4:].lstrip("\n")
    data = {}
    for line in block.splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key.strip()] = [i.strip().strip('"').strip("'") for i in value[1:-1].split(",") if i.strip()]
        else:
            data[key.strip()] = value.strip('"').strip("'")
    return data, body


def links_in(body: str) -> list[str]:
    no_code = re.sub(r"```.*?```", "", body, flags=re.S)
    no_code = re.sub(r"`[^`]*`", "", no_code)
    targets = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", no_code):
        target = re.split(r"[|#^]", raw)[0].strip()
        if target:
            targets.append(target.split("/")[-1])
    return targets


def check(vault: Path) -> tuple[list[str], list[str], int]:
    errors: list[str] = []
    warnings: list[str] = []

    def rel(p: Path) -> str:
        return p.relative_to(vault).as_posix()

    def body_at_head(path: Path) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(vault), "show", f"HEAD:{rel(path)}"], capture_output=True, check=True)
            return parse_frontmatter(out.stdout.decode("utf-8"))[1]
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            return None

    vocab_file = vault / SYSTEM / "_Vocabulary.md"
    vocab: dict[str, set[str]] = {}
    m = re.search(r"```vocab\n(.*?)```", vocab_file.read_text(encoding="utf-8"), re.S) if vocab_file.exists() else None
    if not m:
        errors.append("_Vocabulary.md has no ```vocab``` block")
    else:
        for line in m.group(1).strip().splitlines():
            key, values = line.split(":", 1)
            vocab[key.strip()] = {v.strip() for v in values.split(",")}

    notes = [p for p in vault.rglob("*.md")
             if ".obsidian" not in p.parts and ".trash" not in p.parts and ".git" not in p.parts and "_Templates" not in p.parts]
    names: dict[str, Path] = {}
    for p in notes:
        if p.stem in names:
            errors.append(f"{rel(p)}: same name as {rel(names[p.stem])}, links are ambiguous")
        names[p.stem] = p
    incoming = {p.stem: 0 for p in notes}
    # Images and other files are valid link targets too, such as ![[portrait.webp]].
    attachments = {p.name for p in vault.rglob("*") if p.is_file() and p.suffix and p.suffix != ".md"
                   and ".git" not in p.parts and ".obsidian" not in p.parts}
    aliases: dict[str, str] = {}
    # Notes the user is still writing: they count as link targets, but they are not checked.
    skip = {line.strip() for line in os.environ.get("SEAHORSE_SKIP", "").splitlines() if line.strip()}

    for p in notes:
        path = rel(p)
        if path in skip:
            continue
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        fm, body = parse_frontmatter(text)
        area = p.relative_to(vault).parts[0] if len(p.relative_to(vault).parts) > 1 else ""
        kind = fm.get("type")
        if (area in AREAS_WITH_TYPE or p.parent == vault) and not kind:
            errors.append(f"{path}: missing type field")
        for field, value in fm.items():
            key = f"status.{kind}" if field == "status" else field
            if key in vocab and value and not isinstance(value, list) and value not in vocab[key]:
                errors.append(f"{path}: {field} = '{value}' is not in the Vocabulary")
            if field == "status" and key not in vocab and value:
                errors.append(f"{path}: type '{kind}' takes no status")
        for field in RELATIONS & fm.keys():
            if not isinstance(fm[field], list):
                errors.append(f"{path}: relation {field} must be a list of links")
        if kind in {"wish", "experience", "task"} and fm.get("done") not in {"true", "false"}:
            errors.append(f"{path}: {kind} without done: true or false")
        if kind == "trait" and not fm.get("certainty"):
            errors.append(f"{path}: trait without certainty")
        if kind == "engram":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{4}( \d+)?", p.stem):
                errors.append(f"{path}: engram name is not YYYY-MM-DD HHMM, with an optional suffix")
            old = body_at_head(p)
            if old is not None and old.strip() != body.strip():
                errors.append(f"{path}: engram text was edited. Engrams are immutable")
            if fm.get("state") == "pending":
                try:
                    if datetime.now() - datetime.strptime(fm.get("received", ""), "%Y-%m-%d %H:%M") > timedelta(hours=24):
                        warnings.append(f"{path}: pending for more than 24h")
                except ValueError:
                    errors.append(f"{path}: received is not YYYY-MM-DD HH:MM")
        if kind == "entity":
            for name in [p.stem, *fm.get("aliases", [])]:
                if name.lower() in aliases and aliases[name.lower()] != path:
                    errors.append(f"{path}: name or alias '{name}' duplicates {aliases[name.lower()]}")
                aliases[name.lower()] = path
        owner = fm.get("written_by") == "owner"
        # The user's own notes are never split or reformatted: the text is theirs.
        if not owner and len(text.splitlines()) > MAX_LINES:
            warnings.append(f"{path}: {len(text.splitlines())} lines, split it")
        outside_code = re.sub(r"```.*?```", "CODE", text, flags=re.S)
        # A blank line before a callout separates two cards, such as the days of the week.
        if not owner and re.search(r"\n[ \t]*\n(?!>)", outside_code.strip()):
            warnings.append(f"{path}: has a blank line")
        all_links = links_in(body) + [a for v in fm.values() if isinstance(v, list) for a in links_in(" ".join(v))]
        for target in all_links:
            if target in attachments:
                continue
            if target in names:
                if names[target] != p:
                    incoming[target] += 1
            else:
                # In the user's notes, a link to a page that does not exist yet is a choice, not a defect.
                (warnings if owner else errors).append(f"{path}: broken link [[{target}]]")

    for p in notes:
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        if fm.get("type") == "engram" and fm.get("state") == "integrated" and incoming[p.stem] == 0:
            warnings.append(f"{rel(p)}: marked integrated, but no note cites this engram")
        if fm.get("type") in {"entity", "knowledge"} and incoming[p.stem] == 0:
            warnings.append(f"{rel(p)}: orphan, no note links to it")
    return errors, warnings, len(notes)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    vault = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEAHORSE_VAULT", "."))
    errors, warnings, count = check(vault)
    for e in errors:
        print(f"ERROR  {e}")
    for w in warnings:
        print(f"WARN   {w}")
    print(f"\n{count} notes · {len(errors)} errors · {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
