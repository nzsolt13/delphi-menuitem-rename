#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu_rename.py - Delphi TMenuItem atnevezó.

A Delphi Designer generált menuitem-ek nevében az ekezetes caption
ekezetes karaktereit lecsonkolja: pl. "Oszzesen (netto)" a
"sszesen1", "Sugo" a "Sg1" lett. Ez a szam rekurzivan atnez az
atadott mappaban talalt .pas / .dfm file-ok minden TMenuItem-jét
(separatoron kivul) caption-alap, ekezet nelkuli "mi..." erezve,
esemenykezeloikkel (OnClick, OnDblClick, stb.) ketten, riportot es
TSV-t keszitelve (regi<TAB>uj), az adatbazis-kodoláshoz.

Használat:
    python3 menu_rename.py <mapva>             # dry-run
    python3 menu_rename.py <mappa> --apply     # eles atneznes
    python3 menu_rename.py <mappa> --keep-mi   # mar "mi..." nevu elemek atuj


Dokumentáció: README.md
"""

import os
import re
import sys
import shutil
import datetime
import unicodedata

# ---------------------------------------------------------------------------
# Alap
# ---------------------------------------------------------------------------

MAX_LEN = 60          # nevek hatara (szo-keresztnel kevesbe)
VAG_HAT = 58          # ha a kevesbe is MAX felet, ide vag (szo-keresztnel)

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
# Karakterkódolás
# ---------------------------------------------------------------------------

def detekt_enc(raw: bytes) -> str:
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    # cp1250 magyar karakterek: 0xF5 (ő) 0xF6 (ö) 0xF7 (ő) 0xFB (ű)
    # Ezek UTF-8-ban nem multibyte, hanem "lone byte" - ha van ilyen,
    # a fájl UTF-8-ban érvénytelen -> cp1250.
    try:
        t = raw.decode("utf-8")
        if any(b > 0x7F for b in raw):
            # Nem-ASCII tartalom: UTF8-ban csak multibyte-ok (0xCx 0x8x-0xBF)
            # Ha vannak lone 0xF5-0xFA, a utf-8 decode elbukik vagy érvénytelen.
            return "utf-8"
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1250"


def leolvas(path):
    raw = open(path, "rb").read()
    enc = detekt_enc(raw)
    return raw.decode(enc), enc

def beiras(path, text, enc):
    open(path, "wb").write(text.encode(enc))

# ---------------------------------------------------------------------------
# DFM dekodolás
# ---------------------------------------------------------------------------

def dekod_caption(s):
    out = []
    for num, lit in re.findall(r"#(\d+)|'((?:[^']|'')*)'", s):
        if num:
            out.append(chr(int(num)))
        else:
            out.append(lit.replace("''", "'"))
    return "".join(out)

def fold_words(t):
    """Ekezetet alap-ra csereli, szavakra bont (nem betu/szamot toli).
    A Delphi menutaj (&) nem szóhatár: "m&ásolás" egy szó."""
    t = t.replace("&", "")
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))
    return re.findall(r"[A-Za-z0-9]+", t)

def _pc(w):
    return w[0].upper() + w[1:].lower()

def fold(t):
    return "".join(_pc(w) for w in fold_words(t))

def szeparator(c):
    c = c.strip()
    return c in ("", "-")

# ---------------------------------------------------------------------------
# DFM hierarchia parse
# ---------------------------------------------------------------------------

OBJ_RE = re.compile(r"^\s*object\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\S+)")
END_RE = re.compile(r"^\s*end\b")
CAP_RE = re.compile(r"^\s*Caption\s*=\s*(.+)$", re.I)
ON_RE = re.compile(r"^\s*(On[A-Za-z0-9]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", re.I)
FORM_RE = re.compile(r"^object\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(T[A-Za-z0-9_]+)")


def parse_dfm(text):
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
                p_cap = dekod_caption(cm.group(1))
                continue
            onm = ON_RE.match(line)
            if onm:
                p_on[onm.group(1).lower()] = onm.group(2)
                continue
    commit()
    return items, formtype

# ---------------------------------------------------------------------------
# Nevgenerálás
# ---------------------------------------------------------------------------

def _pascal(words):
    return "".join(_pc(w) for w in words)

def generuj_nev(own, parent_caption, parent_name):
    """Visszaadja az item-nevet vagy None-t (separator)."""
    if szeparator(own):
        return None
    own_words = fold_words(own)
    if not own_words:
        return None

    # prefer: szülökontextus + own, ha nem túl hosszú
    if parent_caption and not szeparator(parent_caption):
        par_words = fold_words(parent_caption)
        if par_words and not _pascal(own_words).lower().startswith(_pascal(par_words).lower()):
            full = "mi" + _pascal(par_words + own_words)
            if len(full) <= MAX_LEN:
                return full

    # egyebe: csak own
    ownfull = "mi" + _pascal(own_words)
    if len(ownfull) <= MAX_LEN:
        return ownfull

    # kevesbe: saját szavait vágjuk szó-keresztnél VAG_HAT alá
    picked = []
    total = 2
    for w in own_words:
        if total + len(w) > VAG_HAT:
            break
        picked.append(w)
        total += len(w)
    if not picked:                      # legalább az első szó belefér
        picked = [own_words[0]]
    return "mi" + _pascal(picked)

# ---------------------------------------------------------------------------
# Atnevezés (szóhatár)
# ---------------------------------------------------------------------------

def szotar(text, old, new):
    pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])")
    return pat.subn(new, text)

def csere_all(text, ren):
    for old in sorted(ren.keys(), key=len, reverse=True):
        text, _ = szotar(text, old, ren[old])
    return text

# ---------------------------------------------------------------------------
# Kollízió-készletek
# ---------------------------------------------------------------------------

IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
def gyujt_ident(text):
    s = set()
    for tok in IDENT_RE.findall(text):
        if tok.lower() in DELPHI_KEYWORDS:
            continue
        s.add(tok)
    return s

# ---------------------------------------------------------------------------
# Fájlok gyűjtése
# ---------------------------------------------------------------------------

def fájlok(mappa):
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

def parner_pas(dfm):
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
# Fő
# ---------------------------------------------------------------------------

def fo(mappa, apply, keep_mi=False):
    mappa = os.path.abspath(mappa)
    files = fájlok(mappa)
    dfms = [f for f in files if f.lower().endswith(".dfm")]

    def rel(p):
        return os.path.relpath(p, mappa)

    report = []      # sorok
    tsv = []         # (rel, old, new) - item-ek
    tsv_h = []       # (rel, old, new) - handler-ek
    form_count = 0
    form_renamed = 0

    for dfm in dfms:
        try:
            text, enc = leolvas(dfm)
        except (OSError, UnicodeDecodeError) as e:
            report.append(f"\n[hiba] {rel(dfm)}: {e}")
            continue
        items, formtype = parse_dfm(text)
        if not items:
            continue
        form_count += 1

        byname = {it["name"]: it for it in items}
        pas = parner_pas(dfm)
        pas_text = ""
        pas_enc = ""
        if pas:
            try:
                pas_text, pas_enc = leolvas(pas)
            except (OSError, UnicodeDecodeError):
                pass

        # Kollízió-készlet: meglévő dfm+pas azonosítók + item-nevek
        taken = set(byname.keys())
        taken |= gyujt_ident(text)
        if pas:
            taken |= gyujt_ident(pas_text)
        taken -= set(it["name"] for it in items)  # a most atnevezett nevek
        taken = set(t for t in taken if t.lower() not in DELPHI_KEYWORDS)

        # 1) item-atnevezés
        ren_item = {}
        for it in items:
            old = it["name"]
            if keep_mi and old.lower().startswith("mi"):
                continue
            new = generuj_nev(it["caption"],
                              byname[it["parent"]]["caption"] if it["parent"] and it["parent"] in byname else "",
                              "" )
            if new is None or new == old:
                continue
            # kollízió + azonosító-kerülés
            cand = new
            if cand in taken:
                i = 1
                while f"{cand}{i}" in taken:
                    i += 1
                cand = f"{cand}{i}"
            taken.add(cand)
            ren_item[old] = cand

        # 2) handler-atnevezés
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

        # Csak akkor érintett a form, ha van legalább 1 átnevezés
        if not ren_item:
            continue
        form_renamed += 1

        # 3) Apply: csere a textben (handler elől, item után)
        new_dfm = csere_all(text, ren_handler)
        new_dfm = csere_all(new_dfm, ren_item)
        new_pas = pas_text
        if pas:
            new_pas = csere_all(pas_text, ren_handler)
            new_pas = csere_all(new_pas, ren_item)

        # Riport
        report.append(f"\nfájl: {rel(dfm)}  (form: {formtype})")
        for old, new in [(it["name"], ren_item[it["name"]]) for it in items if it["name"] in ren_item]:
            cap = byname[old]["caption"]
            report.append(f"  {old:<40} -> {new:<46} | caption: {cap}")
        for old, new in sorted(ren_handler.items()):
            db = 0
            for tt in [text, pas_text]:
                if not tt:
                    continue
                db += len(re.findall(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])", tt))
            report.append(f"  [handler] {old:<38} -> {new:<44} ({db} hivatkozás)")
        seps = [it["name"] for it in items if szeparator(it["caption"])]
        if seps:
            report.append(f"  (separatorok nem érintett: {', '.join(seps)})")

        for old, new in [(it["name"], ren_item[it["name"]]) for it in items if it["name"] in ren_item]:
            tsv.append(f"{rel(dfm)}\t{old}\t{new}")
        for old, new in sorted(ren_handler.items()):
            tsv_h.append(f"{rel(dfm if dfm else '')}\t{old}\t{new}")

        if apply:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(dfm, dfm + f".bak-{stamp}")
            beiras(dfm, new_dfm, enc)
            if pas and new_pas != pas_text:
                shutil.copy2(pas, pas + f".bak-{stamp}")
                beiras(pas, new_pas, pas_enc)

    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "ELES ATNEVEZES" if apply else "DRY-RUN (nem irta a fájlokat)"
    if not report:
        report = ["", f"Nem talaloz atnevezendo menu-elem {mappa} alatt.", ""]
    header = (
        "# menu_rename riport\n"
        f"# mód: {mode}\n"
        f"# mappa: {mappa}\n"
        f"# idő: {date}\n"
        f"# formok ezesé: {form_count}; atnezett form: {form_renamed}\n\n"
        "# TSV mapping: {rel_dfmpath}\tregi\tuj\n"
    )
    rep = header + "\n".join(report) + "\n"

    rep_path = os.path.join(mappa, "menu_rename_report.txt")
    tsv_path = os.path.join(mappa, "menu_rename_mapping.tsv")
    open(rep_path, "w", encoding="utf-8").write(rep)
    all_tsv = tsv + (["# --- handler ---"] + tsv_h if tsv_h else [])
    open(tsv_path, "w", encoding="utf-8").write("\n".join(all_tsv) + ("\n" if all_tsv else ""))

    print(mode)
    print(rep)
    print("Raport:   " + rep_path)
    print("Mapping:  " + tsv_path)
    if apply:
        print("\nBiztonsági mentések: *.bak-<dátum>")


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    keep_mi = "--keep-mi" in args
    args = [a for a in args if a not in ("--apply", "--keep-mi")]
    if len(args) != 1 or not os.path.isdir(args[0]):
        print(__doc__)
        print("használat: python3 menu_rename.py <mappa> [--apply] [--keep-mi]")
        sys.exit(1)
    mappa = args[0]
    if not apply:
        extra = ",  mi* nevek ertek" if keep_mi else ""
        print("=== DRY-RUN MODO ===  (csak raport,  semmit nem ír)  "
               "--- --apply: eles atnevezés ===" + extra, "\n")
    fo(mappa, apply, keep_mi)


if __name__ == "__main__":
    main()
