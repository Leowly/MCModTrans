"""Progress reporting with tqdm/rich integration."""

from __future__ import annotations

import sys
from typing import Iterator, TypeVar

T = TypeVar("T")


def create_progress(
    items: list[T],
    desc: str = "Processing",
    unit: str = "it",
    disable: bool = False,
) -> Iterator[T]:
    """Create a progress bar over items.

    Uses tqdm if available, otherwise falls back to simple iteration.

    Args:
        items: The iterable to wrap.
        desc: Progress bar description.
        unit: Unit label for the counter.
        disable: Force-disable progress bar.

    Yields:
        Each item from the input list.
    """
    try:
        from tqdm import tqdm as _tqdm
        yield from _tqdm(items, desc=desc, unit=unit, disable=disable)
    except ImportError:  # pragma: no cover
        # No tqdm installed — print simple progress
        total = len(items)
        for i, item in enumerate(items, 1):
            if i % 10 == 0 or i == total:
                sys.stderr.write(f"\r{desc}: {i}/{total}")
                sys.stderr.flush()
            yield item
        sys.stderr.write("\n")
