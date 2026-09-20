#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu_rename.py - Renames Delphi TMenuItem components.

The Delphi Designer strips non-ASCII characters out of generated
menu-item names, so accented captions get mangled: e.g.
"Oszzesen (netto)" becomes "sszen1", "Sugo" becomes "Sg1".
This tool walks the given directory recursively, renames every
TMenuItem (except separators) to a clean accented-free "mi..." name
derived from its caption, together with its event handlers
(OnClick, OnDblClick, etc.), and writes a report and a TSV
(old<TAB>new) for database migration.

Usage:
    python3 menu_rename.py <dir>                    # dry-run (no writes)
    python3 menu_rename.py <dir> --apply            # live rename
    python3 menu_rename.py <dir> --keep-mi          # keep items already named mi...
    python3 menu_rename.py <dir> --codepage cp1251  # decode #ddd with cp1251
      (codepage default: cp1250; any Python name like cp1251/cp1252 works)

See README.md for details.
"""

import os
import re
import sys
import shutil
import datetime
import unicodedata

# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------

MAX_LEN = 60          # name length limit (stop below this at a word boundary)
VAG_HAT = 58          # if a word still exceeds MAX_LEN, cut down to this

SKIP_DIRS = {".git", "bin", "__pycache__", ".svn", ".hg"}

DELPHI_KEYWORDS = {
    "and", "array", "as", "asc", "begin", "case", "const", "constructor",
    "destructor", "div", "do", "downto", "else", "end", "except", "exit",
    "file", "finalization", "for", "function", "goto", "if", "in", "inherited",
    "initialization", "inline", "is", "label", "mod", "nil", "not", "object",
    "of", "on", "or", "packed", "procedure", "program", "record", "repeat",
    "set", "shl", "shr", "string", "then", "to", "try", "type", "unit",
    "until", "uses", "var", "while", "with", "xor", "true", "false",
    "tmenuitem", "string", "integer", "boolean",
}

# ---------------------------------------------------------------------------
# Character encoding
# ---------------------------------------------------------------------------

def detect_encoding(raw: bytes, fallback: str = "cp1250") -> str:
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    # Bytes that are not valid UTF-8 are decoded with the fallback code page
    # (cp1250 by default; pass another, e.g. cp1251, via --codepage).
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return fallback


def read_file(path, fallback: str = "cp1250"):
    raw = open(path, "rb").read()
    enc = detect_encoding(raw, fallback)
    return raw.decode(enc), enc

def write_file(path, text, enc):
    open(path, "wb").write(text.encode(enc))

# ---------------------------------------------------------------------------
# DFM decoding / caption helpers
# ---------------------------------------------------------------------------

def decode_caption(s, codepage: str = "cp1250"):
    out = []
    for num, lit in re.findall(r"#(\d+)|'((?:[^']|'')*)'", s):
        if num:
            val = int(num)
            if 0 <= val <= 255:
                out.append(bytes([val]).decode(codepage, errors="replace"))
            else:
                out.append(chr(val))   # Unicode DFM escape
        else:
            out.append(lit.replace("''", "'"))
    return "".join(out)

# Letters that have no NFD decomposition (true characters, not base+diacritic);
# map them to an ASCII form before stripping so they become usable letters.
NON_DECOMPOSED = {
    "\u00DF": "ss",  # ß  German sharp s
    "\u00FE": "th",  # þ  (Þorn)
    "\u00C6": "AE",  # Æ  ligature
    "\u00E6": "ae",
    "\u00D0": "D",   # Ð
    "\u00F0": "d",
    "\u00F8": "o",   # ø
    "\u00E5": "a",   # å
    "\u0110": "D",   # Đ
    "\u0111": "d",
    "\u0141": "L",   # Ł
    "\u0142": "l",
    "\u0195": "T",   # Ŕ
    "\u0196": "t",   # ŕ
    "\u0192": "f",   # ƒ
    "\u0131": "i",   # ı (Turkish dotless i)
    "\u1E9E": "SS",  # ẞ (capital sharp S)
}

def fold_words(t):
    """Strips accents (maps to base letters) and splits into words.
    The Delphi mnemonic marker '&' is not a word boundary: 'm&enu' -> one word."""
    t = "".join(NON_DECOMPOSED.get(c, c) for c in t)
    t = t.replace("&", "")
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))
    return re.findall(r"[A-Za-z0-9]+", t)

def _pc(w):
    return w[0].upper() + w[1:].lower()

def fold(t):
    return "".join(_pc(w) for w in fold_words(t))

def is_separator(c):
    c = c.strip()
    return c in ("", "-")

# ---------------------------------------------------------------------------
# DFM hierarchy parser
# ---------------------------------------------------------------------------

OBJ_RE = re.compile(r"^\s*object\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\S+)")
END_RE = re.compile(r"^\s*end\b")
CAP_RE = re.compile(r"^\s*Caption\s*=\s*(.+)$", re.I)
ON_RE = re.compile(r"^\s*(On[A-Za-z0-9]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", re.I)
FORM_RE = re.compile(r"^object\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(T[A-Za-z0-9_]+)")


def parse_dfm(text, codepage: str = "cp1250"):
    m = FORM_RE.match(text)
    formtype = m.group(2) if m else ""

    items = []
    stack = []
    pending = None
    p_cap = None
    p_on = {}

    def nearest_tmenu(stk):
        for nm, ty in reversed(stk):
            if ty.lower() == "tmenuitem":
                return nm
        return None

    def commit():
        nonlocal pending, p_cap, p_on
        if pending:
            parent = nearest_tmenu(stack)
            items.append({"name": pending, "caption": (p_cap or "").strip(),
                          "on": p_on, "parent": parent})
            pending = None
            p_cap = None
            p_on = {}

    for line in text.splitlines():
        om = OBJ_RE.match(line)
        if om:
            nm, ty = om.group(1), om.group(2)
            if ty.lower() == "tmenuitem":
                commit()
                pending = nm
            else:
                commit()
            stack.append((nm, ty))
            continue
        em = END_RE.match(line)
        if em:
            if stack:
                top = stack.pop()
                if pending and top[0] == pending:
                    commit()
            continue
        if pending:
            cm = CAP_RE.match(line)
            if cm:
                p_cap = decode_caption(cm.group(1), codepage)
                continue
            onm = ON_RE.match(line)
            if onm:
                p_on[onm.group(1).lower()] = onm.group(2)
                continue
    commit()
    return items, formtype

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _pascal(words):
    return "".join(_pc(w) for w in words)

def make_name(own, parent_caption, parent_name):
    """Visszaadja az item-nevet vagy None-t (separator)."""
    if is_separator(own):
        return None
    own_words = fold_words(own)
    if not own_words:
        return None

    # prefer: parent caption + own, if that stays under MAX_LEN
    if parent_caption and not is_separator(parent_caption):
        par_words = fold_words(parent_caption)
        if par_words and not _pascal(own_words).lower().startswith(_pascal(par_words).lower()):
            full = "mi" + _pascal(par_words + own_words)
            if len(full) <= MAX_LEN:
                return full

    # else: only own
    ownfull = "mi" + _pascal(own_words)
    if len(ownfull) <= MAX_LEN:
        return ownfull

    # too long: truncate own's words at a word boundary to stay under VAG_HAT
    picked = []
    total = 2
    for w in own_words:
        if total + len(w) > VAG_HAT:
            break
        picked.append(w)
        total += len(w)
    if not picked:                      # keep at least the first word
        picked = [own_words[0]]
    return "mi" + _pascal(picked)

# ---------------------------------------------------------------------------
# Rename (word/identifier boundary)
# ---------------------------------------------------------------------------

def replace_ident(text, old, new):
    pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])")
    return pat.subn(new, text)

def replace_all(text, ren):
    for old in sorted(ren.keys(), key=len, reverse=True):
        text, _ = replace_ident(text, old, ren[old])
    return text

# ---------------------------------------------------------------------------
# Identifier collection
# ---------------------------------------------------------------------------

IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
def collect_ident(text):
    s = set()
    for tok in IDENT_RE.findall(text):
        if tok.lower() in DELPHI_KEYWORDS:
            continue
        s.add(tok)
    return s

# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_files(mappa):
    out = []
    for root, dirs, files in os.walk(mappa):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith(".") and d != "__pycache__"]
        for f in files:
            if f.lower().endswith((".pas", ".dfm")) and not f.endswith(".bak"):
                full = os.path.join(root, f)
                out.append(full)
    out.sort()
    return out

def partner_pas(dfm):
    stem = os.path.splitext(os.path.basename(dfm))[0].lower()
    d = os.path.dirname(dfm)
    try:
        for f in os.listdir(d):
            if f.lower().endswith(".pas") and os.path.splitext(f)[0].lower() == stem:
                return os.path.join(d, f)
    except OSError:
        pass
    return None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(mappa, apply, keep_mi=False, codepage="cp1250"):
    mappa = os.path.abspath(mappa)
    files = collect_files(mappa)
    dfms = [f for f in files if f.lower().endswith(".dfm")]

    def rel(p):
        return os.path.relpath(p, mappa)

    read_cache = {}
    def read_cached(path):
        if path not in read_cache:
            read_cache[path] = read_file(path, codepage)
        return read_cache[path]

    report = []      # report lines
    tsv = []         # (rel, old, new) - items
    tsv_h = []       # (rel, old, new) - handlers
    form_count = 0
    form_renamed = 0
    parsed = []      # Phase-1 results: one dict per renamed form
    all_pascal = [f for f in files if f.lower().endswith(".pas")]
    all_dfm = [f for f in files if f.lower().endswith(".dfm")]

    # =========================================================================
    # Phase 1: parse every form, compute item + handler renames.  No writing.
    # =========================================================================
    for dfm in dfms:
        try:
            text, enc = read_file(dfm, codepage)
        except (OSError, UnicodeDecodeError) as e:
            report.append(f"\n[hiba] {rel(dfm)}: {e}")
            continue
        items, formtype = parse_dfm(text, codepage)
        if not items:
            continue
        form_count += 1

        byname = {it["name"]: it for it in items}
        pas = partner_pas(dfm)
        pas_text = ""
        pas_enc = ""
        if pas:
            try:
                pas_text, pas_enc = read_file(pas, codepage)
            except (OSError, UnicodeDecodeError):
                pass

        # taken set: existing dfm+pas identifiers + item names
        taken = set(byname.keys())
        taken |= collect_ident(text)
        if pas:
            taken |= collect_ident(pas_text)
        taken -= set(it["name"] for it in items)  # a most atnevezett nevek
        taken = set(t for t in taken if t.lower() not in DELPHI_KEYWORDS)

        # 1) rename items
        ren_item = {}
        for it in items:
            old = it["name"]
            if keep_mi and old.lower().startswith("mi"):
                continue
            new = make_name(it["caption"],
                              byname[it["parent"]]["caption"] if it["parent"] and it["parent"] in byname else "",
                              "" )
            if new is None or new == old:
                continue
            # collision avoidance
            cand = new
            if cand in taken:
                i = 1
                while f"{cand}{i}" in taken:
                    i += 1
                cand = f"{cand}{i}"
            taken.add(cand)
            ren_item[old] = cand

        # 2) rename handlers
        ren_handler = {}
        for it in items:
            if it["name"] not in ren_item:
                continue
            old = it["name"]
            new = ren_item[old]
            for ev, handler in it["on"].items():
                if handler != old and handler.lower().startswith(old.lower()) \
                        and len(handler) > len(old):
                    suffix = handler[len(old):]
                    nh = new + suffix
                    if nh != handler:
                        ren_handler[handler] = nh

        # only affected if there is at least 1 rename
        if not ren_item:
            continue
        form_renamed += 1

        # 3) apply replacements in text (handlers first, then items)
        new_dfm = replace_all(text, ren_handler)
        new_dfm = replace_all(new_dfm, ren_item)
        new_pas = pas_text
        if pas:
            new_pas = replace_all(pas_text, ren_handler)
            new_pas = replace_all(new_pas, ren_item)

        for old, new in [(it["name"], ren_item[it["name"]]) for it in items if it["name"] in ren_item]:
            tsv.append(f"{rel(dfm)}\t{old}\t{new}")
        for old, new in sorted(ren_handler.items()):
            tsv_h.append(f"{rel(dfm if dfm else '')}\t{old}\t{new}")

        # keep all state for the later report / cross-form safety / apply phases
        parsed.append({
            "dfm": dfm, "text": text, "enc": enc, "formtype": formtype,
            "items": items, "byname": byname,
            "pas": pas, "pas_text": pas_text, "pas_enc": pas_enc,
            "ren_item": ren_item, "ren_handler": ren_handler,
            "new_dfm": new_dfm, "new_pas": new_pas,
            "seps": [it["name"] for it in items if is_separator(it["caption"])],
        })

    # =========================================================================
    # Phase 2: cross-form handler safety.
    # A handler can be safely renamed in EVERY .pas/.dfm only if exactly one
    # form in the parsed set declares it (procedure/function [Unit.]Handler( )
    # or a class-qualified call like  Unit.Handler(  ).  If two forms define
    # their own handler with the same identifier (Delphi only guarantees
    # uniqueness PER unit), a blind global replace would corrupt the other
    # one, so those stay local and must be reviewed manually.
    # =========================================================================
    global_handler = {}   # old -> new, safe to rename across the whole project
    ambig_handler = {}    # old -> new, declared by more than one form
    all_form_handlers = {}
    for p in parsed:
        for old in p["ren_handler"]:
            all_form_handlers.setdefault(old, set()).add(p["dfm"])
    for p in parsed:
        own = p["ren_handler"]
        for old in own:
            if old not in all_form_handlers or len(all_form_handlers[old]) == 1:
                # only one form in the project owns a handler named `old`
                # -> every occurrence is a ref to THIS handler, safe to rename
                # across the whole project (including cross-unit calls like
                #  Unit.ClassHandler(  or  Class.ClassHandler(  ).
                global_handler[old] = own[old]
            else:
                ambig_handler[old] = own[old]

    # The set of .pas/.dfm files that a global handler rename will reach
    # beyond the owning form's own pair (drives the "also updated" reporting).
    global_target = [f for f in sorted(set(all_pascal) | set(all_dfm))]

    # =========================================================================
    # Phase 3 + 4: build per-form report, apply renames, then global handler
    # rename across the whole project.
    # =========================================================================
    for p in parsed:
        dfm = p["dfm"]; text = p["text"]; enc = p["enc"]
        items = p["items"]; byname = p["byname"]
        pas = p["pas"]; pas_text = p["pas_text"]; pas_enc = p["pas_enc"]
        ren_item = p["ren_item"]; ren_handler = p["ren_handler"]

        report.append(f"\nfile: {rel(dfm)}  (form: {p['formtype']})")
        for old, new in [(it["name"], ren_item[it["name"]]) for it in items if it["name"] in ren_item]:
            cap = byname[old]["caption"]
            report.append(f"  {old:<40} -> {new:<46} | caption: {cap}")
        for old, new in sorted(ren_handler.items()):
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])")
            db = 0
            for tt in [text, pas_text]:
                if tt:
                    db += len(pat.findall(tt))
            if old in global_handler:
                report.append(f"  [handler] {old:<38} -> {new:<44} ({db} refs)  -- GLOBAL rename (single declaration, safe)")
            else:
                # defined by more than one unit -> per-form rename only,
                # and any other unit that calls it must be reviewed manually.
                other = []
                for f in files:
                    if f == dfm or f == pas:
                        continue
                    if f.lower().endswith(".dfm") or os.path.basename(f).lower().endswith(".pas"):
                        try:
                            tt, _ = read_cached(f)
                        except (OSError, UnicodeDecodeError):
                            continue
                        c = len(pat.findall(tt))
                        if c:
                            other.append((rel(f), c))
                report.append(f"  [handler] {old:<38} -> {new:<44} ({db} refs)  -- AMBIGUOUS: shared by several units, NOT renamed globally")
                if other:
                    lst = ", ".join(f"{pp} ({n}x)" for pp, n in other)
                    report.append(f"          !! external refs to '{old}' NOT renamed - check manually: {lst}")
        if p["seps"]:
            report.append(f"  (separators untouched: {', '.join(p['seps'])})")

        if apply:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(dfm, dfm + f".bak-{stamp}")
            write_file(dfm, p["new_dfm"], enc)
            if pas and p["new_pas"] != pas_text:
                shutil.copy2(pas, pas + f".bak-{stamp}")
                write_file(pas, p["new_pas"], pas_enc)

    # global handler rename: apply the safe old->new to every .pas/.dfm.
    # Files are read fresh from disk (so per-form files already written above
    # come back updated, where this is a no-op; the cross-referencing units are
    # still at their old text and get updated here).
    if apply and global_handler:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        for f in global_target:
            if not (f.lower().endswith(".dfm") or f.lower().endswith(".pas")):
                continue
            try:
                tt, fe = read_file(f, codepage)
            except (OSError, UnicodeDecodeError):
                continue
            nn = tt
            for old, new in sorted(global_handler.items(), key=lambda kv: len(kv[0]), reverse=True):
                nn, _ = replace_ident(nn, old, new)
            if nn != tt:
                shutil.copy2(f, f + f".bak-global-{stamp}")
                write_file(f, nn, fe)
                report.append(f"  [global-handler] updated cross-form ref in {rel(f)}")

    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "LIVE RENAME" if apply else "DRY-RUN (files not written)"
    if not report:
        report = ["", f"No menu items to rename under {mappa}.", ""]
    header = (
        "# menu_rename report\n"
        f"# mode: {mode}\n"
        f"# dir: {mappa}\n"
        f"# time: {date}\n"
        f"# forms scanned: {form_count}; forms renamed: {form_renamed}\n\n"
        "# TSV mapping: {rel_dfmpath}\told\tnew\n"
    )
    rep = header + "\n".join(report) + "\n"

    rep_path = os.path.join(mappa, "menu_rename_report.txt")
    tsv_path = os.path.join(mappa, "menu_rename_mapping.tsv")
    open(rep_path, "w", encoding="utf-8").write(rep)
    all_tsv = tsv + (["# --- handler ---"] + tsv_h if tsv_h else [])
    open(tsv_path, "w", encoding="utf-8").write("\n".join(all_tsv) + ("\n" if all_tsv else ""))

    print(mode)
    print(rep)
    print("Report:   " + rep_path)
    print("Mapping:  " + tsv_path)
    if apply:
        print("\nBackups: *.bak-<timestamp>")


def main():
    args = sys.argv[1:]
    apply   = "--apply" in args
    keep_mi = "--keep-mi" in args

    # optional: --codepage <name>   (default: cp1250)
    codepage = "cp1250"
    if "--codepage" in args:
        i = args.index("--codepage")
        if i + 1 >= len(args):
            print("Error: --codepage requires a value, e.g. cp1251")
            sys.exit(1)
        codepage = args[i + 1]
        args = args[:i] + args[i + 2:]

    args = [a for a in args if a not in ("--apply", "--keep-mi")]
    if len(args) != 1 or not os.path.isdir(args[0]):
        print(__doc__)
        print("usage: python3 menu_rename.py <dir> [--apply] [--keep-mi] [--codepage CP]")
        sys.exit(1)
    mappa = args[0]
    if not apply:
        cp_note = f", codepage={codepage}" if codepage != "cp1250" else ""
        extra = ", keeping existing mi* names" if keep_mi else ""
        print("=== DRY-RUN ===  (report/mapping only, nothing written)  "
               f"--- --apply: live rename ==={extra}{cp_note}", "\n")
    run(mappa, apply, keep_mi, codepage=codepage)


if __name__ == "__main__":
    main()
