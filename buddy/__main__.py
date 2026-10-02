"""Allow `python -m buddy` when the `buddy` script is not on PATH."""

from buddy.cli import app

app(prog_name="buddy")
