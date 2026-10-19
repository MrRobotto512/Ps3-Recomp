#!/usr/bin/env python3
"""Apply a reference port's patches to this port's generated code, where they fit.

    python scripts/apply_port_patches.py <inFAMOUSreborn>/installer/patches --work . \
        [--address-map eu_to_us.txt]

A port that ships patches it applies over freshly generated code (inFAMOUSreborn:
installer/patches/infamous-recomp/{recompiled,spu}/...) keeps fixes there that its
toolkit depends on -- ppu_recomp.h gains what that toolkit's runtime expects. Each
patch names its file under the port's own folder; that first path component is
dropped and the rest is looked up in --work (recompiled/..., spu/...). Embedded SPU
images are named after their address in the executable (spu_0000_at_005F6500), which
differs between releases of a game, so those are matched by their number instead.

Hunks are placed by content, not line number: each hunk's original lines must occur
exactly in the file (line endings aside), and the line number only picks between
several matches. A hunk whose result is already there counts as applied. A hunk
that does not fit is left out and counted -- never forced. A `# eol lf` (or crlf)
line in a patch sets the file's line endings, as the port's installer does.

--address-map makes another release's PPU patches usable. The lifted PPU code names
guest addresses (func_00353A80, loc_000CA540, ctx->lr = 0x0039D98C); each is
translated through the map, and the hunk then goes to whichever chunk file defines
its labels, since the chunks of two releases need not split at the same functions.
A hunk with no address in it (the declarations at the top of every chunk) goes to
every chunk where it fits. The map is text -- `EU US` address pairs, or
`EU_START EU_END DELTA` ranges, one per line, # comments -- or JSON: {"EU": "US"},
[[EU, US], ...] or {"ranges": [[EU_START, EU_END, DELTA], ...]}. Between anchors an
address takes the offset of the nearest anchor below it.
"""

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

ADDR_TOKEN = re.compile(r"\b(func_|loc_|0x)([0-9A-Fa-f]{8})\b")
LABEL_DEF = re.compile(r"^\s*(?:loc_([0-9A-F]{8}):|(?:extern \"C\" )?void func_([0-9A-F]{8})\(ppu_context\*[^;]*$)")


# --------------------------------------------------------------------------- patches

class Hunk:
    def __init__(self, old_start: int):
        self.old_start = old_start
        self.old: list[str] = []
        self.new: list[str] = []


def parse_patch(text: str) -> tuple[str | None, list[tuple[str, list[Hunk]]]]:
    """Unified diff -> (its `# eol` directive, [(path from +++, minus b/, hunks)])."""
    files, hunks, eol = [], None, None
    for line in text.replace("\r\n", "\n").split("\n"):
        if hunks is None and line.startswith("# eol "):
            eol = line[6:].strip().lower()
        elif line.startswith("+++ ") and (hunks is None or not hunks or _done(hunks[-1])):
            path = line[4:].split("\t")[0]
            path = path[2:] if path[:2] in ("a/", "b/") else path
            hunks = []
            files.append((path, hunks))
        elif line.startswith("--- ") and (hunks is None or not hunks or _done(hunks[-1])):
            continue
        elif line.startswith("@@") and hunks is not None:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))?", line)
            h = Hunk(int(m.group(1)) if m else 0)
            h.want_old = int(m.group(2) or 1) if m else 0
            h.want_new = int(m.group(3) or 1) if m else 0
            hunks.append(h)
        elif hunks and not _done(hunks[-1]):
            h = hunks[-1]
            if line.startswith("-"):
                h.old.append(line[1:])
            elif line.startswith("+"):
                h.new.append(line[1:])
            elif line.startswith(" ") or line == "":   # "" = a blank context line stripped of its space
                h.old.append(line[1:]); h.new.append(line[1:])
    return eol, files


def _done(h: Hunk) -> bool:
    """A hunk is complete once it holds the line counts its @@ header promised."""
    return len(h.old) >= h.want_old and len(h.new) >= h.want_new


# --------------------------------------------------------------------------- address map

class AddressMap:
    def __init__(self, anchors: list[tuple[int, int]]):
        anchors = sorted(dict(anchors).items())
        self.keys = [a for a, _ in anchors]
        self.deltas = [d for _, d in anchors]
        self.lo, self.hi = self.keys[0], self.keys[-1]

    @classmethod
    def load(cls, path: Path) -> "AddressMap":
        text = path.read_text(encoding="utf-8", errors="replace")
        num = lambda v: v if isinstance(v, int) else int(str(v), 0)
        anchors: list[tuple[int, int]] = []
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and "ranges" in data:
            for lo, hi, d in data["ranges"]:
                anchors += [(num(lo), num(d)), (num(hi), num(d))]
        elif isinstance(data, dict):
            anchors = [(num(k), num(v) - num(k)) for k, v in data.items()]
        elif isinstance(data, list):
            anchors = [(num(a), num(b) - num(a)) for a, b in data]
        else:
            for line in text.splitlines():
                f = line.split("#")[0].replace(",", " ").split()
                if len(f) == 2:
                    anchors.append((int(f[0], 0), int(f[1], 0) - int(f[0], 0)))
                elif len(f) == 3:
                    lo, hi, d = (int(x, 0) for x in f)
                    anchors += [(lo, d), (hi, d)]
        if not anchors:
            sys.exit(f"no addresses in {path}")
        return cls(anchors)

    def __call__(self, addr: int) -> int:
        return addr + self.deltas[max(bisect.bisect_right(self.keys, addr) - 1, 0)]

    def translate(self, line: str) -> str:
        def sub(m):
            a = int(m.group(2), 16)
            if not self.lo <= a <= self.hi:   # not a code address the map knows: leave it
                return m.group(0)
            digits = f"{self(a):08X}"
            return m.group(1) + (digits if m.group(2) == m.group(2).upper() else digits.lower())
        return ADDR_TOKEN.sub(sub, line)


# --------------------------------------------------------------------------- applying

class Target:
    """A file being patched. Every hunk is placed against the file as generated,
    through one index; the edits are made at save time, bottom-up, so a placement
    never shifts another. The file keeps its own line endings."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.crlf = b"\r\n" in raw[:65536]
        self.lines = raw.decode("utf-8", errors="surrogateescape").replace("\r\n", "\n").split("\n")
        self.changed = False
        self.edits: list[tuple[int, int, list[str]]] = []   # (start, old length, new lines)
        self._starts: dict[str, list[int]] | None = None

    def occurrences(self, block: list[str]) -> list[int]:
        if not block:
            return []
        if self._starts is None:
            self._starts = {}
            for i, line in enumerate(self.lines):
                self._starts.setdefault(line, []).append(i)
        n = len(block)
        return [i for i in self._starts.get(block[0], ()) if self.lines[i:i + n] == block]

    def _free(self, at: int, n: int) -> bool:
        return all(at + max(n, 1) <= s or s + max(k, 1) <= at for s, k, _ in self.edits)

    def apply(self, h: Hunk, near: int | None = None, after: int = 0) -> str:
        """Place h: at the occurrence nearest `near` if given, else the first at or past
        `after` (else the first at all). Returns applied / already / missed."""
        spots = [i for i in self.occurrences(h.old) if self._free(i, len(h.old))]
        if spots:
            if near is not None:
                at = min(spots, key=lambda i: abs(i - near))
            else:
                at = next((i for i in spots if i >= after), spots[0])
            self.edits.append((at, len(h.old), h.new))
            self.changed = True
            h.placed = at
            return "applied"
        if h.new != h.old and self.occurrences(h.new):
            return "already"
        return "missed"

    def save(self) -> None:
        if not self.changed:
            return
        lines = self.lines
        for at, n, new in sorted(self.edits, key=lambda e: e[0], reverse=True):
            lines[at:at + n] = new
        text = "\n".join(lines)
        if self.crlf:
            text = text.replace("\n", "\r\n")
        self.path.write_bytes(text.encode("utf-8", "surrogateescape"))


def locate(work: Path, target: Path) -> Path | None:
    if (work / target).is_file():
        return target
    m = re.fullmatch(r"(spu_\d{4})_at_[0-9A-Fa-f]+", target.parts[1]) if len(target.parts) > 2 else None
    if target.parts[0] == "spu" and m:
        for d in sorted((work / "spu").glob(f"{m.group(1)}_at_*")):
            alt = Path("spu", d.name, *target.parts[2:])
            if (work / alt).is_file():
                return alt
    return None


def label_index(chunks: list[Target]) -> dict[str, tuple[Target, int]]:
    """loc_/func_ address -> (the chunk that defines it, the line it is defined on)."""
    where = {}
    for t in chunks:
        for i, line in enumerate(t.lines):
            if "loc_" in line or "func_" in line:
                m = LABEL_DEF.match(line)
                if m:
                    where.setdefault(m.group(1) or m.group(2), (t, i))
    return where


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patches", type=Path, help="the reference port's patch folder")
    ap.add_argument("--work", type=Path, required=True, help="the folder holding recompiled/ and spu/")
    ap.add_argument("--address-map", type=Path, help="the reference port's release -> this one's (see above)")
    args = ap.parse_args()

    patches = sorted(args.patches.rglob("*.patch"))
    if not patches:
        sys.exit(f"no .patch files under {args.patches}")
    amap = AddressMap.load(args.address_map) if args.address_map else None

    opened: dict[Path, Target] = {}
    def target(rel: Path) -> Target:
        if rel not in opened:
            opened[rel] = Target(args.work / rel)
        return opened[rel]

    chunks, labels = [], {}
    if amap:
        chunks = [target(p.relative_to(args.work))
                  for p in sorted((args.work / "recompiled").glob("ppu_recomp_*.cpp"))]
        labels = label_index(chunks)

    totals = {"applied": 0, "already": 0, "missed": 0}
    report = []
    last: dict[Path, int] = {}   # per chunk: where the previous hunk landed (hunks come in order)
    for p in patches:
        eol, files = parse_patch(p.read_text(encoding="utf-8", errors="surrogateescape"))
        for name, hunks in files:
            rel = Path(*Path(name).parts[1:])                       # infamous-recomp/x -> x
            ppu_chunk = bool(amap) and rel.parent == Path("recompiled") \
                and rel.name.startswith("ppu_recomp_") and rel.suffix == ".cpp"
            here = locate(args.work, rel)
            if here is None and not ppu_chunk:
                report.append((rel.as_posix(), len(hunks), "no such file here"))
                continue
            counts = {"applied": 0, "already": 0, "missed": 0}
            if eol in ("lf", "crlf") and here is not None:
                t = target(here)
                if t.crlf != (eol == "crlf"):
                    t.crlf, t.changed = eol == "crlf", True
            for h in hunks:
                if not ppu_chunk:
                    counts[target(here).apply(h)] += 1
                    continue
                h.old = [amap.translate(l) for l in h.old]
                h.new = [amap.translate(l) for l in h.new]
                # Where in this release's lift: a label the hunk defines is exact; one it
                # branches to is defined nearby, in the same function. Identical code
                # recurs in many functions, so position matters as much as content.
                defined = [m.group(1) or m.group(2) for l in h.old for m in [LABEL_DEF.match(l)] if m]
                used = [m.group(2).upper() for l in h.old for m in ADDR_TOKEN.finditer(l)
                        if m.group(1) == "loc_"]
                anchor = next((labels[a] for a in defined + used if a in labels), None)
                if anchor:
                    t, line = anchor
                    if defined and anchor == labels.get(defined[0]):
                        # the hunk starts this many lines before its own label
                        line -= next(k for k, l in enumerate(h.old) if LABEL_DEF.match(l))
                    counts[t.apply(h, near=line)] += 1
                    last[t.path] = getattr(h, "placed", last.get(t.path, 0))
                elif any(ADDR_TOKEN.search(l) for l in h.old):
                    t = target(here) if here else None
                    status = t.apply(h, after=last.get(t.path, 0)) if t else "missed"
                    counts[status] += 1
                    if t and status == "applied":
                        last[t.path] = h.placed
                else:          # no address at all: shared text, e.g. each chunk's declarations
                    results = [c.apply(h) for c in chunks]
                    counts["applied" if "applied" in results else
                           "already" if "already" in results else "missed"] += 1
            for k, v in counts.items():
                totals[k] += v
            report.append((rel.as_posix(), len(hunks), f"{counts['applied']} applied, "
                           f"{counts['already']} already, {counts['missed']} did not fit"))

    for t in opened.values():
        t.save()
    for path, n, what in report:
        print(f"{path}: {n} hunk(s): {what}")
    print(f"\n{totals['applied'] + totals['already']} of {sum(totals.values())} hunks in place "
          f"({totals['applied']} applied now, {totals['already']} already, {totals['missed']} did not fit).")


if __name__ == "__main__":
    main()
