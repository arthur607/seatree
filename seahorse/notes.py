"""Read and write markdown notes with a simple frontmatter."""
import re
from pathlib import Path


def read(path: Path) -> tuple[dict, str]:
    """The frontmatter as a dict and the body. Lists written inline, `[a, b]`, become Python lists."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    data = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key.strip()] = [i.strip().strip('"\'') for i in value[1:-1].split(",") if i.strip()]
        else:
            data[key.strip()] = value.strip('"\'')
    return data, text[end + 4:].lstrip("\n")


def set_field(path: Path, key: str, value: str) -> None:
    """Adds or replaces one frontmatter field without touching the body."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end == -1:
        return
    lines = [line for line in text[4:end].splitlines() if not line.startswith(f"{key}:")]
    lines.append(f"{key}: {value}")
    path.write_text("---\n" + "\n".join(lines) + text[end:], encoding="utf-8")


def quoted(value: str) -> str:
    """A value that is safe on one frontmatter line."""
    clean = " ".join(str(value).split()).replace('"', "'")
    return f'"{clean}"'


def without_blank_lines(text: str) -> str:
    return re.sub(r"\n\s*\n+", "\n", text.strip())


def links(text: str) -> list[str]:
    """The targets of the `[[wikilinks]]` in a text, without aliases or headings."""
    return [re.split(r"[|#^]", target)[0].strip() for target in re.findall(r"\[\[([^\]]+)\]\]", text)]
