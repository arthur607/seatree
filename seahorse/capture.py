"""A capture from outside Telegram, such as a Claude Code session on a computer.

It reads the text from standard input, saves the engram, runs the Integrator and prints what was done.
Usage: seahorse capture [claude|obsidian|other] < text.txt
"""
import sys
from datetime import datetime

from . import agents, config, notes, vault

CHANNELS = {"claude", "obsidian", "other"}


def capture(text: str, channel: str = "other") -> str:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel}")
    engram = vault.new_engram(text, channel, datetime.now(config.TIMEZONE))
    answer = agents.integrator() or ""
    result = agents.parse_results(answer).get(engram.stem)
    if not result:
        # Another Integrator, such as the bot's, can take this engram first.
        state = notes.read(engram)[0].get("state", "pending")
        result = "integrated" if state == "integrated" else f"state: {state}"
    return f"{engram.stem} => {result}"


def main(channel: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    text = sys.stdin.buffer.read().decode("utf-8").strip()
    if not text:
        sys.exit("empty text")
    print(capture(text, channel))
