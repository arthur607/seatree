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
# Characters that break a file name on some systems, or a wikilink in Obsidian.
BAD_CHARACTERS = '\\ / : * ? " < > | # ^ [ ]'
BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


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
    texts = {p: p.read_text(encoding="utf-8").replace("\r\n", "\n") for p in notes}
    # Notes the user is still writing: they count as link targets, but they are not checked.
    skip = {line.strip() for line in os.environ.get("SEAHORSE_SKIP", "").splitlines() if line.strip()}

    def owned(p: Path) -> bool:
        """The user's own notes, imported ones included, and the notes the user is still writing. Their text and
        their name belong to the user: what lint finds in them is a warning, so no fix run tries to change them."""
        fm = parse_frontmatter(texts[p])[0]
        return fm.get("written_by") == "owner" or bool(fm.get("imported_from")) or rel(p) in skip

    names: dict[str, Path] = {}
    for p in notes:
        if p.stem in names:
            (warnings if owned(p) or owned(names[p.stem]) else errors).append(
                f"{rel(p)}: same name as {rel(names[p.stem])}, links are ambiguous")
        names[p.stem] = p
        if BAD_NAME.search(p.stem):
            (warnings if owned(p) else errors).append(
                f"{rel(p)}: the file name has a character that breaks a link or a file name, one of {BAD_CHARACTERS}")
    incoming = {p.stem: 0 for p in notes}
    # Images and other files are valid link targets too, such as ![[portrait.webp]].
    attachments = {p.name for p in vault.rglob("*") if p.is_file() and p.suffix and p.suffix != ".md"
                   and ".git" not in p.parts and ".obsidian" not in p.parts}
    aliases: dict[str, str] = {}

    for p in notes:
        path = rel(p)
        if path in skip:
            continue
        text = texts[p]
        fm, body = parse_frontmatter(text)
        area = p.relative_to(vault).parts[0] if len(p.relative_to(vault).parts) > 1 else ""
        kind = fm.get("type")
        owner = owned(p)
        # In a note of the user an error is a warning: no fix run may change their text.
        bad = warnings if owner else errors
        if (area in AREAS_WITH_TYPE or p.parent == vault) and not kind:
            bad.append(f"{path}: missing type field")
        for field, value in fm.items():
            key = f"status.{kind}" if field == "status" else field
            if key in vocab and value and not isinstance(value, list) and value not in vocab[key]:
                bad.append(f"{path}: {field} = '{value}' is not in the Vocabulary")
            if field == "status" and key not in vocab and value:
                bad.append(f"{path}: type '{kind}' takes no status")
        for field in RELATIONS & fm.keys():
            if not isinstance(fm[field], list):
                bad.append(f"{path}: relation {field} must be a list of links")
        if kind in {"wish", "experience", "task"} and fm.get("done") not in {"true", "false"}:
            bad.append(f"{path}: {kind} without done: true or false")
        if kind == "trait" and not fm.get("certainty"):
            bad.append(f"{path}: trait without certainty")
        if path.startswith(PEOPLE + "/"):
            _check_person(path, fm, bad, warnings)
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
                    bad.append(f"{path}: name or alias '{name}' duplicates {aliases[name.lower()]}")
                aliases[name.lower()] = path
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
                bad.append(f"{path}: broken link [[{target}]]")

    for p in notes:
        fm, _ = parse_frontmatter(texts[p])
        if fm.get("type") == "engram" and fm.get("state") == "integrated" and incoming[p.stem] == 0:
            warnings.append(f"{rel(p)}: marked integrated, but no note cites this engram")
        if fm.get("type") in {"entity", "knowledge"} and incoming[p.stem] == 0:
            warnings.append(f"{rel(p)}: orphan, no note links to it")
    _check_weeks(vault, errors, warnings)
    return errors, warnings, len(notes)


PEOPLE = "2. 📓 Memory/People"
PERSON_FIELDS = {"birthday": "YYYY-MM-DD, or --MM-DD when the year is unknown",
                 "keep_in_touch": "weekly, monthly, quarterly, or a number of days such as 30d",
                 "last_contact": "YYYY-MM-DD"}


def _check_person(path: str, fm: dict, bad: list[str], warnings: list[str]) -> None:
    """The fields of a People page that code reads for birthdays and calls. A value that code cannot read is an
    error. A value that code reads, in another form than the Vocabulary asks for, is a warning."""
    try:
        from seahorse import people  # the same reader the code uses
    except Exception:  # noqa: BLE001 - lint still checks the rest without it
        return
    readers = {"birthday": people.parse_birthday, "keep_in_touch": people.parse_cadence,
               "last_contact": people.parse_day}
    for field, form in PERSON_FIELDS.items():
        value = fm.get(field)
        if not value:
            continue
        if isinstance(value, list) or not readers[field](value):
            bad.append(f"{path}: {field} = '{value}' is not readable, write {form}")
        elif field != "keep_in_touch" and people.ambiguous(value):
            warnings.append(f"{path}: {field} = '{value}' can be day/month or month/day, write {form}")
        elif not people.CANONICAL[field].fullmatch(value):
            warnings.append(f"{path}: {field} = '{value}', write {form}")


def _check_weeks(vault: Path, errors: list[str], warnings: list[str]) -> None:
    """Each week file has seven day cards that the code can read. A problem in the current or a coming week is
    an error, because code plans with it; in a past week it is a warning."""
    folder = vault / "1. 🎯 Intention" / "Week"
    if not folder.is_dir():
        return
    try:
        from seahorse import week  # the same reader the code uses
    except Exception:  # noqa: BLE001 - lint still checks the rest without it
        return
    year, number, _ = datetime.now().date().isocalendar()
    current = f"{year}-W{number:02d}"
    for path in sorted(folder.glob("*.md")):
        for problem in week.problems(path):
            line = f"{path.relative_to(vault).as_posix()}: {problem}"
            (errors if path.stem >= current else warnings).append(line)


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
