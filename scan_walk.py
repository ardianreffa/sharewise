"""Walk one folder with the guard applied to every entry (same rules as Spacewise)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterable

from . import guard

OTHER_MEDIA = {".avi", ".mkv", ".webm", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".tif", ".tiff", ".gif"}


def walk(root: Path, recursive: bool, skipped: dict) -> Iterable[tuple[Path, os.stat_result]]:
    def note(reason: str):
        e = skipped.setdefault(reason, [0, 0]); e[0] += 1

    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            note("could not be read"); continue
        for e in sorted(entries, key=lambda x: x.name):
            p = Path(e.path)
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                note("could not be read"); continue
            why = guard.is_hidden_or_locked(p, st)
            if why:
                note(why); continue
            if stat.S_ISDIR(st.st_mode):
                if p.name == "Sharewise":
                    continue                                  # never re-check our own output folder
                prot = guard.is_protected_dir(p)
                if prot:
                    note(prot); continue
                if recursive:
                    stack.append(p)
                else:
                    note("subfolder (not checked; ask for subfolders to include them)")
                continue
            if stat.S_ISREG(st.st_mode):
                yield p, st
