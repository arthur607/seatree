"""The week, and today, as an image to read on Telegram without opening Obsidian.

Minimal, in a typewriter font: thin rules, text and `[ ]` / `[x]` boxes. Three days per row, and the summary of
the week in the space next to Sunday. The height grows with the fullest day.
"""
import io
from dataclasses import dataclass
from datetime import date, datetime

from PIL import Image, ImageDraw, ImageFont

from . import config, i18n, tasks, week

FONTS = config.CODE / "fonts"
PAPER, INK, FAINT, MUTED, RULE, ACCENT, WARNING = "#FBFAF6", "#16191F", "#8A8F98", "#B9BDC4", "#DEDBD2", "#2372F5", "#D9532C"
WIDTH, MARGIN, COLUMNS = 1920, 56, 3


@dataclass
class Line:
    name: str
    minutes: int
    done: bool
    warning: str = ""


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf")), size)


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


def _lines(day: week.Day, task_notes: dict, today: date, end: date) -> list[Line]:
    found = []
    for item in day.items:
        note = task_notes.get(item.name)
        done = item.done or bool(note and note.done)
        found.append(Line(item.name, item.minutes, done, "" if done else warning(note.due if note else None, today, end)))
    return found


def _progress(d: ImageDraw.ImageDraw, x: int, y: int, width: int, squares: int, done: int, total: int, height: int = 22) -> None:
    full = round(squares * done / total) if total else 0
    side = (width - (squares - 1) * 6) // squares
    for k in range(squares):
        d.rectangle((x + k * (side + 6), y, x + k * (side + 6) + side, y + height), fill=ACCENT if k < full else RULE)


def draw_week(w: week.Week, today: date, work: set[str], task_notes: dict) -> Image.Image:
    gap, row_gap = 43, 62
    col = (WIDTH - 2 * MARGIN - (COLUMNS - 1) * gap) // COLUMNS
    f_title, f_sub, f_day, f_text, f_small, f_big = _font(55, True), _font(36), _font(36, True), _font(31), _font(28), _font(62, True)
    r = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    indent = int(r.textlength("[ ] ", font=f_text))
    text_width = col - indent - int(r.textlength("2h30", font=f_small)) - 34
    step, between = 46, 19
    end = w.days[-1].date
    days = i18n.words("days_short")

    sizes = []
    for day in w.days:
        blocks, height = [], 0
        for line in _lines(day, task_notes, today, end):
            wrapped = _wrap(r, line.name, f_text, text_width)
            h = len(wrapped) * step + (38 if line.warning else 0)
            blocks.append((line, wrapped, h))
            height += h + between
        sizes.append((blocks, height))
    rows = [list(range(i, min(i + COLUMNS, len(w.days)))) for i in range(0, len(w.days), COLUMNS)]
    if len(rows[-1]) == COLUMNS:
        rows.append([])
    heights = [max(276, 94 + max([sizes[i][1] for i in row] or [0])) for row in rows]
    top = 206
    img = Image.new("RGB", (WIDTH, top + sum(heights) + row_gap * (len(rows) - 1) + MARGIN), PAPER)
    d = ImageDraw.Draw(img)

    year, number, _ = w.days[0].date.isocalendar()
    a, b = w.days[0].date, w.days[-1].date
    title = f"{i18n.t('image.title')} {number}"
    d.text((MARGIN, 67), title, font=f_title, fill=INK)
    d.text((MARGIN + d.textlength(title, font=f_title) + 36, 84),
           f"{year} · {a.day:02d}/{a.month:02d} → {b.day:02d}/{b.month:02d}", font=f_sub, fill=FAINT)
    d.line((MARGIN, 166, WIDTH - MARGIN, 166), fill=RULE, width=2)

    total = sum(day.total(work) for day in w.days)
    capacity = sum(day.capacity for day in w.days)
    done = sum(line.done for blocks, _ in sizes for line, _, _ in blocks)
    count = sum(len(blocks) for blocks, _ in sizes)

    y = top
    for row, height in zip(rows, heights):
        for column, i in enumerate(row):
            day, (blocks, _) = w.days[i], sizes[i]
            x = MARGIN + column * (col + gap)
            is_today, past = day.date == today, day.date < today
            label = f"{days[day.date.weekday()]} {day.date.day:02d}" + (f" ← {i18n.t('image.today')}" if is_today else "")
            d.text((x, y), label, font=f_day, fill=ACCENT if is_today else (MUTED if past else INK))
            d.text((x + col, y + 6), f"{week.duration(day.total(work))}/{week.duration(day.capacity)}", font=f_small,
                   fill=ACCENT if is_today else (MUTED if past else FAINT), anchor="ra")
            d.line((x, y + 60, x + col, y + 60), fill=ACCENT if is_today else RULE, width=4 if is_today else 2)
            ty = y + 84
            if not blocks:
                d.text((x, ty), "—", font=f_text, fill=MUTED)
            for line, wrapped, h in blocks:
                faded = line.done or past
                color = MUTED if line.done else (FAINT if past else INK)
                d.text((x, ty), "[x]" if line.done else "[ ]", font=f_text, fill=MUTED if faded else FAINT)
                for j, piece in enumerate(wrapped):
                    d.text((x + indent, ty + j * step), piece, font=f_text, fill=color)
                    if line.done:
                        middle = ty + j * step + 23
                        d.line((x + indent, middle, x + indent + d.textlength(piece, font=f_text), middle), fill=MUTED, width=2)
                if line.minutes:
                    d.text((x + col, ty + 4), week.duration(line.minutes), font=f_small, fill=MUTED if faded else FAINT, anchor="ra")
                if line.warning:
                    d.text((x + indent, ty + len(wrapped) * step), f"! {line.warning}", font=f_small, fill=WARNING)
                ty += h + between
        y += height + row_gap

    # The summary takes the columns left free in the last row.
    used = len(rows[-1])
    x, y0 = MARGIN + used * (col + gap), top + sum(heights[:-1]) + row_gap * (len(rows) - 1)
    width = WIDTH - MARGIN - x
    d.text((x, y0), i18n.t("image.summary"), font=f_day, fill=INK)
    d.line((x, y0 + 60, x + width, y0 + 60), fill=RULE, width=2)
    d.text((x, y0 + 84), week.duration(total), font=f_big, fill=ACCENT)
    d.text((x + d.textlength(week.duration(total), font=f_big) + 17, y0 + 110),
           f"/ {week.duration(capacity)} {i18n.t('image.planned')}", font=f_text, fill=FAINT)
    _progress(d, x, y0 + 190, width, 20 * (COLUMNS - used), done, count)
    d.text((x, y0 + 231), f"{done}/{count} {i18n.t('image.done')}", font=f_small, fill=FAINT)
    return img


def draw_day(w: week.Week, today: date, work: set[str], task_notes: dict) -> Image.Image:
    """Only today, in the same style as the week: narrower, with bigger letters."""
    width, margin = 1280, 64
    day = next(d for d in w.days if d.date == today)
    f_title, f_sub, f_text, f_small, f_count = _font(62, True), _font(34), _font(38), _font(32), _font(34, True)
    r = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    indent = int(r.textlength("[ ] ", font=f_text))
    text_width = width - 2 * margin - indent - int(r.textlength("2h30", font=f_small)) - 40
    step, between = 54, 26
    blocks, body = [], 0
    for line in _lines(day, task_notes, today, w.days[-1].date):
        wrapped = _wrap(r, line.name, f_text, text_width)
        h = len(wrapped) * step + (44 if line.warning else 0)
        blocks.append((line, wrapped, h))
        body += h + between
    top = 232
    img = Image.new("RGB", (width, top + max(body, 80) + 150), PAPER)
    d = ImageDraw.Draw(img)

    year, number, _ = today.isocalendar()
    label = f"{i18n.words('days_short')[today.weekday()]} {today.day:02d}"
    d.text((margin, 66), label, font=f_title, fill=ACCENT)
    d.text((margin + d.textlength(label, font=f_title) + 34, 88),
           f"{today.day:02d}/{today.month:02d}/{year} · {i18n.t('image.title')} {number}", font=f_sub, fill=FAINT)
    d.text((width - margin, 88), f"{week.duration(day.total(work))}/{week.duration(day.capacity)}",
           font=f_sub, fill=ACCENT, anchor="ra")
    d.line((margin, 176, width - margin, 176), fill=ACCENT, width=4)

    y = top
    if not blocks:
        d.text((margin, y), f"— {i18n.t('image.nothing')}", font=f_text, fill=MUTED)
    for line, wrapped, h in blocks:
        d.text((margin, y), "[x]" if line.done else "[ ]", font=f_text, fill=MUTED if line.done else FAINT)
        for j, piece in enumerate(wrapped):
            d.text((margin + indent, y + j * step), piece, font=f_text, fill=MUTED if line.done else INK)
            if line.done:
                middle = y + j * step + 28
                d.line((margin + indent, middle, margin + indent + d.textlength(piece, font=f_text), middle), fill=MUTED, width=3)
        if line.minutes:
            d.text((width - margin, y + 5), week.duration(line.minutes), font=f_small, fill=MUTED if line.done else FAINT, anchor="ra")
        if line.warning:
            d.text((margin + indent, y + len(wrapped) * step), f"! {line.warning}", font=f_small, fill=WARNING)
        y += h + between

    base = img.height - 110
    done, count = sum(line.done for line, _, _ in blocks), len(blocks)
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
