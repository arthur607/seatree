"""The week, and today, as an image to read on Telegram without opening Obsidian.

Minimal, in a typewriter font: thin rules, text and `[ ]` / `[x]` boxes. Two days per row, so the text is big
enough on a phone, and the summary of the week in the space next to Sunday. The height grows with the fullest
day, up to a limit: Telegram refuses a photo when its width and height add up to more than 10000 pixels.
"""
import io
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache

from PIL import Image, ImageDraw, ImageFont

from . import config, i18n, tasks, week

FONTS = config.CODE / "fonts"
PAPER, INK, FAINT, MUTED, RULE, ACCENT, WARNING = "#FBFAF6", "#16191F", "#8A8F98", "#B9BDC4", "#DEDBD2", "#2372F5", "#D9532C"
WIDTH, MARGIN, COLUMNS = 1280, 48, 2
# The body of one day in the week image, and of the day image, at most. Past this, the rest is "+N more".
DAY_BODY, TODAY_BODY = 1700, 7400
INVISIBLE = {"‍", "︎", "️"}  # joiners and emoji selectors: they draw nothing


@dataclass
class Line:
    name: str
    minutes: int
    done: bool
    warning: str = ""
    work: bool = False


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf")), size)


@cache
def _has_glyph(char: str) -> bool:
    """The typewriter font has no emoji: a character it does not have would show as an empty box."""
    if char.isascii():
        return True
    font = _font(32)
    missing = font.getmask("￿")
    mask = font.getmask(char)
    return (mask.size, bytes(mask)) != (missing.size, bytes(missing))


def clean(text: str) -> str:
    """The text without the characters the font cannot draw, such as emoji."""
    kept = "".join(c for c in text if c not in INVISIBLE and _has_glyph(c))
    return " ".join(kept.split()) or "·"


def _wrap(d: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """Breaks at words. A word that does not fit a line alone is cut with a hyphen, so it never overflows."""
    lines, current = [], ""
    for word in text.split():
        attempt = f"{current} {word}".strip()
        if current and d.textlength(attempt, font=font) > width:
            lines.append(current)
            attempt = word
        while d.textlength(attempt, font=font) > width and len(attempt) > 2:
            cut = len(attempt) - 1
            while cut > 1 and d.textlength(attempt[:cut] + "-", font=font) > width:
                cut -= 1
            lines.append(attempt[:cut] + "-")
            attempt = attempt[cut:]
        current = attempt
    return lines + [current] if current else lines


def warning(due: date | None, today: date, end: date) -> str:
    """Only a real due date gives a warning. A task without `due` is never late."""
    if not due or due > end:
        return ""
    if due < today:
        return i18n.t("image.late")
    if due == today:
        return i18n.t("image.due_today")
    return i18n.t("image.due_on", day=i18n.words("days_short")[due.weekday()])


def day_month(day: date) -> str:
    """05/10 or, in English, 10/05."""
    return i18n.t("image.day_month", day=f"{day.day:02d}", month=f"{day.month:02d}")


def full_date(day: date) -> str:
    return i18n.t("image.full_date", day=f"{day.day:02d}", month=f"{day.month:02d}", year=str(day.year))


def _lines(day: week.Day, task_notes: dict, today: date, end: date, work: set[str]) -> list[Line]:
    found = []
    for item in day.items:
        note = task_notes.get(item.name)
        done = item.done or bool(note and note.done)
        found.append(Line(clean(item.name), item.minutes, done,
                          "" if done else warning(note.due if note else None, today, end), item.name in work))
    return found


def _blocks(r: ImageDraw.ImageDraw, lines: list[Line], font, width: int, step: int, extra: int, between: int,
            limit: int) -> tuple[list, int, int]:
    """Each line wrapped, with its height, up to `limit` pixels. Returns the blocks, how many lines did not fit,
    and the height."""
    blocks, height = [], 0
    for n, line in enumerate(lines):
        wrapped = _wrap(r, line.name, font, width)
        h = len(wrapped) * step + (extra if line.warning else 0)
        room_for_more = step + between if n < len(lines) - 1 else 0
        if height + h + between + room_for_more > limit:
            return blocks, len(lines) - n, height + step + between
        blocks.append((line, wrapped, h))
        height += h + between
    return blocks, 0, height


def _hours(day: week.Day, work: set[str]) -> tuple[str, bool]:
    """"1h30/4h", and if the day is over its limit."""
    total = day.total(work)
    return f"{week.duration(total)}/{week.duration(day.capacity)}", total > day.capacity


def _duration(line: Line) -> str:
    """A work task shows its time in parentheses: it does not count in the hours of the day."""
    return f"({week.duration(line.minutes)})" if line.work else week.duration(line.minutes)


def _progress(d: ImageDraw.ImageDraw, x: int, y: int, width: int, squares: int, done: int, total: int, height: int = 22) -> None:
    full = round(squares * done / total) if total else 0
    side = (width - (squares - 1) * 6) // squares
    for k in range(squares):
        d.rectangle((x + k * (side + 6), y, x + k * (side + 6) + side, y + height), fill=ACCENT if k < full else RULE)


def _task_lines(d, x: int, y: int, right: int, indent: int, blocks: list, hidden: int, past: bool, fonts: tuple,
                step: int, between: int) -> None:
    """The tasks of one day, from (x, y), with their time at the right edge."""
    f_text, f_small = fonts
    for line, wrapped, h in blocks:
        faded = line.done or past
        color = MUTED if line.done else (FAINT if past else INK)
        d.text((x, y), "[x]" if line.done else "[ ]", font=f_text, fill=MUTED if faded else FAINT)
        for j, piece in enumerate(wrapped):
            d.text((x + indent, y + j * step), piece, font=f_text, fill=color)
            if line.done:
                middle = y + j * step + step // 2
                d.line((x + indent, middle, x + indent + d.textlength(piece, font=f_text), middle), fill=MUTED, width=2)
        if line.minutes:
            d.text((right, y + 4), _duration(line), font=f_small, fill=MUTED if faded or line.work else FAINT, anchor="ra")
        if line.warning:
            d.text((x + indent, y + len(wrapped) * step), f"! {line.warning}", font=f_small, fill=WARNING)
        y += h + between
    if hidden:
        d.text((x + indent, y), i18n.t("image.more", n=hidden), font=f_small, fill=FAINT)


def draw_week(w: week.Week, today: date, work: set[str], task_notes: dict) -> Image.Image:
    gap, row_gap = 40, 56
    col = (WIDTH - 2 * MARGIN - (COLUMNS - 1) * gap) // COLUMNS
    f_title, f_sub, f_day, f_text, f_small, f_big = _font(52, True), _font(30), _font(36, True), _font(33), _font(27), _font(58, True)
    r = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    indent = int(r.textlength("[ ] ", font=f_text))
    text_width = col - indent - int(r.textlength("(2h30)", font=f_small)) - 20
    step, between = 44, 16
    end = w.days[-1].date
    days = i18n.words("days_short")

    sizes, all_lines = [], []
    for day in w.days:
        lines = _lines(day, task_notes, today, end, work)
        all_lines += lines
        sizes.append(_blocks(r, lines, f_text, text_width, step, 36, between, DAY_BODY))
    rows = [list(range(i, min(i + COLUMNS, len(w.days)))) for i in range(0, len(w.days), COLUMNS)]
    if len(rows[-1]) == COLUMNS:
        rows.append([])
    heights = [max(190, 90 + max([sizes[i][2] for i in row] or [0])) for row in rows]
    heights[-1] = max(heights[-1], 300)  # room for the summary
    top = 190
    img = Image.new("RGB", (WIDTH, top + sum(heights) + row_gap * (len(rows) - 1) + MARGIN), PAPER)
    d = ImageDraw.Draw(img)

    year, number, _ = w.days[0].date.isocalendar()
    title = f"{i18n.t('image.title')} {number}"
    d.text((MARGIN, 56), title, font=f_title, fill=INK)
    d.text((MARGIN + d.textlength(title, font=f_title) + 30, 72),
           f"{year} · {day_month(w.days[0].date)} → {day_month(w.days[-1].date)}", font=f_sub, fill=FAINT)
    d.line((MARGIN, 146, WIDTH - MARGIN, 146), fill=RULE, width=2)

    y = top
    for row, height in zip(rows, heights):
        for column, i in enumerate(row):
            day, (blocks, hidden, _) = w.days[i], sizes[i]
            x = MARGIN + column * (col + gap)
            is_today, past = day.date == today, day.date < today
            label = f"{days[day.date.weekday()]} {day.date.day:02d}" + (f" ← {i18n.t('image.today')}" if is_today else "")
            d.text((x, y), label, font=f_day, fill=ACCENT if is_today else (MUTED if past else INK))
            hours, over = _hours(day, work)
            d.text((x + col, y + 6), hours, font=f_small, anchor="ra",
                   fill=WARNING if over else ACCENT if is_today else (MUTED if past else FAINT))
            d.line((x, y + 58, x + col, y + 58), fill=ACCENT if is_today else RULE, width=4 if is_today else 2)
            if not blocks and not hidden:
                d.text((x, y + 80), "—", font=f_text, fill=MUTED)
            _task_lines(d, x, y + 80, x + col, indent, blocks, hidden, past, (f_text, f_small), step, between)
        y += height + row_gap

    # The summary takes the column left free in the last row.
    total = sum(day.total(work) for day in w.days)
    capacity = sum(day.capacity for day in w.days)
    done, count = sum(line.done for line in all_lines), len(all_lines)
    used = len(rows[-1])
    x, y0 = MARGIN + used * (col + gap), top + sum(heights[:-1]) + row_gap * (len(rows) - 1)
    width = WIDTH - MARGIN - x
    d.text((x, y0), i18n.t("image.summary"), font=f_day, fill=INK)
    d.line((x, y0 + 58, x + width, y0 + 58), fill=RULE, width=2)
    d.text((x, y0 + 80), week.duration(total), font=f_big, fill=WARNING if total > capacity else ACCENT)
    d.text((x + d.textlength(week.duration(total), font=f_big) + 16, y0 + 104),
           f"/ {week.duration(capacity)} {i18n.t('image.planned')}", font=f_small, fill=FAINT)
    _progress(d, x, y0 + 180, width, 10 * (COLUMNS - used), done, count)
    d.text((x, y0 + 218), f"{done}/{count} {i18n.t('image.done')}", font=f_small, fill=FAINT)
    if any(line.work for line in all_lines):
        d.text((x, y0 + 256), i18n.t("image.work"), font=f_small, fill=MUTED)
    return img


def draw_day(w: week.Week, today: date, work: set[str], task_notes: dict) -> Image.Image:
    """Only today, in the same style as the week: one column, with bigger letters."""
    width, margin = 1280, 64
    day = w.day(today) or week.Day("", "", week.minutes(config.DAILY_CAPACITY) or 240, today)
    f_title, f_sub, f_text, f_small, f_count = _font(62, True), _font(34), _font(38), _font(32), _font(34, True)
    r = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    indent = int(r.textlength("[ ] ", font=f_text))
    text_width = width - 2 * margin - indent - int(r.textlength("(2h30)", font=f_small)) - 40
    step, between = 54, 26
    lines = _lines(day, task_notes, today, w.days[-1].date, work)
    blocks, hidden, body = _blocks(r, lines, f_text, text_width, step, 44, between, TODAY_BODY)
    legend = 56 if any(line.work for line in lines) else 0
    top = 232
    img = Image.new("RGB", (width, top + max(body, 80) + legend + 150), PAPER)
    d = ImageDraw.Draw(img)

    year, number, _ = today.isocalendar()
    label = f"{i18n.words('days_short')[today.weekday()]} {today.day:02d}"
    d.text((margin, 66), label, font=f_title, fill=ACCENT)
    d.text((margin + d.textlength(label, font=f_title) + 34, 88),
           f"{full_date(today)} · {i18n.t('image.title')} {number}", font=f_sub, fill=FAINT)
    hours, over = _hours(day, work)
    d.text((width - margin, 88), hours, font=f_sub, fill=WARNING if over else ACCENT, anchor="ra")
    d.line((margin, 176, width - margin, 176), fill=ACCENT, width=4)

    if not lines:
        d.text((margin, top), f"— {i18n.t('image.nothing')}", font=f_text, fill=MUTED)
    _task_lines(d, margin, top, width - margin, indent, blocks, hidden, False, (f_text, f_small), step, between)

    base = img.height - 110
    if legend:
        d.text((margin, base - 28 - legend), i18n.t("image.work"), font=f_small, fill=MUTED)
    done, count = sum(line.done for line in lines), len(lines)
    d.line((margin, base - 28, width - margin, base - 28), fill=RULE, width=2)
    label = f"{done}/{count} {i18n.t('image.done')}"
    _progress(d, margin, base + 10, width - 2 * margin - int(d.textlength(label, font=f_count)) - 36, 24, done, count, 24)
    d.text((width - margin, base + 22), label, font=f_count, fill=INK, anchor="rm")
    return img


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def _render(draw, today: date | None) -> bytes | None:
    today = today or datetime.now(config.TIMEZONE).date()
    w = week.read(today)
    if not w:
        return None
    return _png(draw(w, today, tasks.work_tasks(), {t.name: t for t in tasks.all_tasks()}))


def week_png(today: date | None = None) -> bytes | None:
    """The PNG of the week that holds the day, or None when no week is planned."""
    return _render(draw_week, today)


def day_png(today: date | None = None) -> bytes | None:
    """The PNG of only today, or None when no week is planned."""
    return _render(draw_day, today)


# ---- for other images in the same style, such as the retrospective (retro.py) ------------------------------

def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """The typewriter font of the images."""
    return _font(size, bold)


def wrap(d: ImageDraw.ImageDraw, text: str, face, width: int) -> list[str]:
    """The text broken into lines that fit the width."""
    return _wrap(d, text, face, width)


def progress(d: ImageDraw.ImageDraw, x: int, y: int, width: int, squares: int, done: int, total: int,
             height: int = 22) -> None:
    """A row of squares: the first ones, for what is done, in the accent colour."""
    _progress(d, x, y, width, squares, done, total, height)


def png(image: Image.Image) -> bytes:
    return _png(image)
