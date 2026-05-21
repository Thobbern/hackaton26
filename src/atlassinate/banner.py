"""Pretty ASCII banners for the gonfluence/gira CLIs.

Output goes to stderr so JSON on stdout stays clean. Disabled when stderr
is not a TTY (CI, pipes, MCP stdio) or when GONFLUENCE_NO_BANNER is set.
"""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.text import Text

from atlassinate import __version__

GONFLUENCE_BIG = r"""
 ██████╗  ██████╗ ███╗   ██╗███████╗██╗     ██╗   ██╗███████╗███╗   ██╗ ██████╗███████╗
██╔════╝ ██╔═══██╗████╗  ██║██╔════╝██║     ██║   ██║██╔════╝████╗  ██║██╔════╝██╔════╝
██║  ███╗██║   ██║██╔██╗ ██║█████╗  ██║     ██║   ██║█████╗  ██╔██╗ ██║██║     █████╗
██║   ██║██║   ██║██║╚██╗██║██╔══╝  ██║     ██║   ██║██╔══╝  ██║╚██╗██║██║     ██╔══╝
╚██████╔╝╚██████╔╝██║ ╚████║██║     ███████╗╚██████╔╝███████╗██║ ╚████║╚██████╗███████╗
 ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚══════╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
"""

GONFLUENCE_SMALL = r"""
   ___          __ _
  / __|___ _ _ / _| |_  _ ___ _ _  __ ___
 | (_ / _ \ ' \  _| | || / -_) ' \/ _/ -_)
  \___\___/_||_|_| |_|\_,_\___|_||_\__\___|
"""

GIRA_BIG = r"""
 ██████╗ ██╗██████╗  █████╗
██╔════╝ ██║██╔══██╗██╔══██╗
██║  ███╗██║██████╔╝███████║
██║   ██║██║██╔══██╗██╔══██║
╚██████╔╝██║██║  ██║██║  ██║
 ╚═════╝ ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝
"""

GIRA_SMALL = r"""
   ___ _
  / __(_)_ _ __ _
 | (_ | | '_/ _` |
  \___|_|_| \__,_|
"""

GIRAFFE_ART = r"""
      __
     /_/\__
    ( o.o /
     \___/|
         ||
"""

TAGLINES = {
    "gonfluence": "🦒  confluence ↔ markdown  ·  trust · blame · rag  🦒",
    "gira": "🦒  jira from the terminal  🦒",
}

GIRAFFE_COLOR = "rgb(218,165,32)"

# (start_rgb, end_rgb) for the left→right gradient
THEMES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "gonfluence": ((56, 139, 253), (118, 224, 165)),  # confluence blue → mint
    "gira": ((45, 109, 244), (140, 215, 245)),         # jira blue → sky
}


def _gradient_line(
    line: str,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    total_cols: int,
) -> Text:
    text = Text()
    span = max(1, total_cols - 1)
    for i, ch in enumerate(line):
        if ch == " ":
            text.append(ch)
            continue
        t = min(1.0, i / span)
        r = int(start[0] + (end[0] - start[0]) * t)
        g = int(start[1] + (end[1] - start[1]) * t)
        b = int(start[2] + (end[2] - start[2]) * t)
        text.append(ch, style=f"rgb({r},{g},{b})")
    return text


def _banner_for(name: str, width: int) -> str | None:
    big = {"gonfluence": GONFLUENCE_BIG, "gira": GIRA_BIG}.get(name)
    small = {"gonfluence": GONFLUENCE_SMALL, "gira": GIRA_SMALL}.get(name)
    if big is None:
        return None
    big_width = max((len(ln) for ln in big.strip("\n").splitlines()), default=0)
    if width >= big_width + 2:
        return big
    small_width = max((len(ln) for ln in small.strip("\n").splitlines()), default=0) if small else 0
    if small and width >= small_width + 2:
        return small
    return None


def print_banner(name: str, subtitle: str | None = None) -> None:
    """Render the big gradient banner to stderr. No-op when disabled."""
    if os.environ.get("GONFLUENCE_NO_BANNER"):
        return
    if not sys.stderr.isatty():
        return

    err = Console(stderr=True, highlight=False)
    banner = _banner_for(name, err.size.width)
    start, end = THEMES.get(name, ((140, 140, 140), (200, 200, 200)))

    err.print()
    if banner is None:
        err.print(
            f"  [bold rgb({start[0]},{start[1]},{start[2]})]▸ {name.upper()}[/]  "
            f"[dim]· {TAGLINES.get(name, '')}[/dim]"
        )
        err.print()
        return

    lines = banner.strip("\n").splitlines()
    cols = max(len(ln) for ln in lines)

    is_big = banner == {"gonfluence": GONFLUENCE_BIG, "gira": GIRA_BIG}.get(name)
    if is_big:
        giraffe_lines = GIRAFFE_ART.strip("\n").splitlines()
        giraffe_w = max(len(ln) for ln in giraffe_lines)
        pad = max(0, (cols - giraffe_w) // 2)
        for gline in giraffe_lines:
            err.print(f"[{GIRAFFE_COLOR}]{' ' * pad}{gline}[/]")

    for line in lines:
        err.print(_gradient_line(line, start, end, cols))

    tag = subtitle or TAGLINES.get(name, "")
    footer = f"  [dim italic]{tag}[/dim italic]  [dim]· v{__version__}[/dim]"
    err.print(footer)
    err.print()
