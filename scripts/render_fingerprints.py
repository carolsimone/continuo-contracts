"""Write `docs/fingerprints.md` from the packaged `fingerprints.yaml`.

Run it from anywhere: `uv run python scripts/render_fingerprints.py`. The output path is found
from this script's own location.
"""

import logging
import sys
from pathlib import Path

from continuo_contracts.fingerprints import load, render_markdown

logger = logging.getLogger(__name__)

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "fingerprints.md"


def main() -> int:
    """Write the rendered reference to `OUTPUT` and return the exit status."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_markdown(load()), encoding="utf-8", newline="\n")
    logger.info("wrote %s", OUTPUT)
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
