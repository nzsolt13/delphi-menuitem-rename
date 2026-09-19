# menu_rename

A renaming tool for Delphi `TMenuItem` components.

The Delphi Designer strips non-ASCII characters out of generated menu-item
names, so non-English captions (e.g. Hungarian) get mangled: "Összesen
(nettó)" becomes `sszen1`, "Súgó" becomes `Sg1`, "Áfakulcs megadás" becomes
`fakulcsmegads1`. These names are hard to read, and the original intent of the
`Caption` can no longer be recovered from them.

This tool walks given directories recursively, finds `.pas` and `.dfm` files,
and renames generated menu-item names to clean, readable, ASCII-only `mi…`
names derived from the caption. It also renames the associated event handlers
(`Click`, etc.) and all references (`.pas` declarations and implementations,
call sites, plus `.dfm` `OnClick=`/`OnDblClick=` assignments). Separator
items (empty or `-` caption) are left untouched.

The tool writes two outputs into the target directory:

- `menu_rename_report.txt` – a human-readable report
- `menu_rename_mapping.tsv` – machine-readable `rel_path<TAB>old<TAB>new`,
  useful for migrating the database "favorite menu items" table

## Usage

```bash
python3 menu_rename.py <dir>                   # DRY-RUN: report/mapping only, no writes
python3 menu_rename.py <dir> --apply           # LIVE: rename .pas and .dfm files
python3 menu_rename.py <dir> --keep-mi         # keep items already named mi…
python3 menu_rename.py <dir> --apply --keep-mi # LIVE + keep-mi
```

`<dir>` is the root of a Delphi project (or any directory containing
`.pas`/`.dfm` files). The walk is recursive and skips `.git`, `bin`, and
`__pycache__` directories.

| flag | effect |
|------|--------|
| `--apply` | actually modify files (with timestamped backup copies) |
| `--keep-mi` | skip items whose current name already starts with `mi…` (and their handlers) |

`--keep-mi` is useful when a previous run already produced `mi…` names you
want to keep, while still renaming the remaining (still-generated) names.

### Safety

- The default mode is DRY-RUN: files are not modified, only the report and
  mapping TSV are written.
- With `--apply`, a `.bak-<YYYYMMDD_HHMMSS>` backup of every modified
  `.pas`/`.dfm` file is created before writing, so changes are always
  revertible.
- Output files (`report`, `tsv`, `.bak*`) are listed in `.gitignore` and are
  not committed. Delphi source files stay in your own project repo (this
  tool does not version them).

## Event handlers / references

- If a renamed menu item has `OnClick = X…Click` (or any `On*=`), the
  handler name is rewritten according to the new item name, and every
  reference to that handler is updated: `.pas` declaration and
  implementation, `.dfm` `On*=` assignments, plus any other object that
  calls the same handler (e.g. a button).
- A non-`TMenuItem` object's (button, grid) `OnClick=…Click` value is also
  rewritten if that handler's name starts with the renamed item's name.
- Replacement happens only at Pascal identifier boundaries: dotted access
  like `Form1.sszsen1` is rewritten, but a partial match like `sszen1X` is
  not.

## Naming rules

The new name is built from the caption, prefixed with `mi`:

1. **Deaccent**: accented letters are mapped to their base form (á→a, é→e,
   í→i, ó→o, ú→u, ö/ő→o, ü/ű→u); other non-alphabetic characters (space,
   punctuation, parentheses, `&`, …) are discarded. "Összesen (nettó)" →
   `OsszesenNetto`.
2. **PascalCase**: each word is capitalized.
3. **Parent context**: if the direct parent is also a menu item and the
   child's caption does not already start with the parent's caption, the
   parent's caption (folded) is prepended — this avoids collisions between
   two items with the same caption.
4. **Length limit** (>60 chars): the name is truncated at a word boundary,
   keeping at most 58 characters, to stay readable.
5. **Collisions**: if the resulting name already exists within the same form
   (another menu item or any other identifier), a `1`, `2`, … suffix is
   appended. Parent context plus suffixing guarantees two items in the same
   form never end up with the same name.

> **Note**: items that were already manually renamed (e.g.
> `miExcelFajlBetoltese`, `TetelekOsszesen`) are also renamed if they do not
> match the caption-derived name — that is the "rename every item" rule. If
> you want to keep a specific existing name, check the report before running
> `--apply`.

## Encodings

- `.dfm`: written back in the same encoding it was read.
- `.pas`: detects UTF-8 (with or without BOM) or cp1250 (Hungarian ANSI);
  the output is written back in the same encoding.
- **CRLF/line endings**: the tool preserves the original line-ending
  characters; only the identifiers are replaced.

## Sample report (short)

```
file: menu-rename/u_biz_tetel.dfm  (form: TAbl_biz_tetel)
  Mgsem1                  ->  miMegsem                 | caption: Mégsem
  Mgsem2                  ->  miMegsem1                | caption: Mégsem
  Sg1                     ->  miSugo                   | caption: Súgó
  Engedmny1               ->  miEngedmeny              | caption: Engedmény
  Kszlet1                 ->  miArEsKeszlet            | caption: Ár és készlet
  fakulcsmegads1          ->  miAfakulcsMegadas        | caption: Áfakulcs megadás
  sszenblegysgrszmts1     ->  miOsszenbolEgysegarSzamitasO | caption: Osszenbol egysegar szamitas (o)
  ...
  [handler] Engedmny1Click        -> miEngedmenyClick        (4 references)
  [handler] fakulcsmegads1Click   -> miAfakulcsMegadasClick  (3 references)
  (separators untouched: N1)

Summary: X items, Y handlers, Z forms.
```

TSV rows look like `u_biz_tetel.dfm<TAB>Mgsem1<TAB>miMegsem`. You can feed
this into a DB migration, e.g.:

```sql
UPDATE kedvencmenu
SET nev = VALUES(nev)
FROM (
  VALUES ('Mgsem1','miMegsem'), ...
) AS m(old, new)
WHERE kedvencmenu.nev = m.old;
```

## File layout

```
menu-rename/
  menu_rename.py         – the tool
  README.md              – this file
  .gitignore             – test fixtures, report, tsv, .bak
  u_biz_tetel.dfm/.pas   – gitignored (test fixture)
  U_FORG.dfm/.PAS        – gitignored (test fixture)
```

## Git

- `menu-rename/` is its own git repo; `menu_rename.py` and `README.md` are
  versioned.
- The tool never commits on your behalf.
- The `.gitignore` excludes the Delphi fixtures (`.pas`, `.dfm`), the report
  (`menu_rename_report.txt`, `menu_rename_mapping.tsv`) and any `.bak*`
  backups.
