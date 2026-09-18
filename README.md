# menu_rename

Delphi `TMenuItem`-ek átnevező eszköze. A Delphi Designer a generált
menüpontok nevénél az ékezetes karaktereket lecsupaszolja, ezért a magyar
caption-ek torzulnak: pl. "Összesen (nettó)" → `sszen1`, "Súgó" → `Sg1`,
"Áfakulcs megadás" → `fakulcsmegads1`. Ezek nehezen olvashatóak, és a
`Caption`ból már nem állítható vissza a szándék.

Ez a szerszág rekurzívan átnézi a megadott mappában a `.pas` és `.dfm`
fájlokat, és a generált nevű menüpontokat új, ékezet nélküli, olvasható `mi…`
nevekre cseréli (a caption alapján), a hozzájuk tartozó eseménykezelők
(`Click` stb.) neveit is, valamint az összes más hivatkozást (`.pas` deklaráció,
implementáció, hívás; `.dfm` `OnClick=`/`OnDblClick=`). A separátor menüpontokat
(üres vagy `-` caption) nem érinti.

Két kimenetet ír a cél mappába:

- `menu_rename_report.txt` – ember-olvasható riport
- `menu_rename_mapping.tsv` – gépi `relatív_útvonal<TAB>regi<TAB>új`, az
  adatbázis "kedvenc menüpontjai" táblájának átkódolásához

## Használat

```bash
python3 menu_rename.py <mappa>                   # DRY-RUN: csak riport/mapping, nem ír
python3 menu_rename.py <mappa> --apply           # ÉLES: átnevezi a .pas és .dfm fájlokat
python3 menu_rename.py <mappa> --keep-mi         # a már mi…-vel kezdődő nevek nem változnak
python3 menu_rename.py <mappa> --apply --keep-mi # ÉLES + keep-mi
```

`<mappa>` a Delphi projekt gyökerének elérési útja. A szerszág rekurzívan bejárt,
`.git`, `bin`, `__pycache__` mappákat kihagyva.

| flag | hatás |
|------|-------|
| `--apply` | írja a fájlokat (biztonsági másolattal) |
| `--keep-mi` | az eddigi `mi…`-vel kezdődő menüpontok és eseménykezelőik kikerülik az átnevezést |

`--keep-mi` akkor hasznos, ha egy korábbi futás már adott `mi…` nevet, amit most
meg akarsz őrizni, miközben a többi (még generált nevű) elemet átnevezed.

### Biztonság

- alapértelmezésben DRY-RUN: a fájlokat nem módosítja, csak riportot és TSV-t ír;
- `--apply` futás előtt minden módosított `.pas`/`.dfm`-fájlból
  `.bak-<éév-hó-nap>_<óó-pp-mm>` másolat készül, így bármikor visszavonható;
- a kimeneti fájlok (report, tsv, `.bak*`) a `.gitignore`-ban vannak, nem kerülnek
  git-be; a Delphi forrásfájlokat a saját git-repódban kezeled (a szerszág azokat
  nem versionzi).

## Eseménykezelők / hivatkozások

- Ha egy átnevezett menüpont `OnClick = X…Click` (másik `On*` is) event-re
  köti, a szerszág a kezelő névét is átírja az új név szerint, és minden
  hivatkozást (`.pas` deklaráció + implementáció, `.dfm` `On*` assignment, valamint
  bármely más objektum, amely ugyanazt a kezelőt hívja – pl. gombok).
- Egy nem-`TMenuItem` objektum (gomb, grid) `OnClick=…Click` value is átíródik,
  ha a kezelő az átnevezett menüpont nevével kezdődik.
- A csere Pascal-azonosító-határok között történik: `Form1.sszsen1` pontozott
  hozzáférés is átíródik, de `sssen1X` (rész-szó) nem.

## Névgenerálás szabályai

Az új név a captionból épül, `mi` prefixszel:

1. **Ékezet-eltávolítás**: az ékezetet alap-hangra cseréli (á→a, é→e, í→i, ó→o,
   ú→u, ö/ő→o, ü/ű→u), a többi nem-betű karaktert (központ, kötőjel, zárójel,
   `&`, ...) elhagyja. "Összesen (nettó)" → `OsszesenNetto`.
2. **PascalCase**: minden szav nagy kezdőbetűvel.
3. **Szülő-kontextus**: ha a menüpont közvetlen szülője is menüpont, és a szülő
   captionje nincs a gyermek captionjében, akkor a szülő caption fold-olt neve
   prefixként bekapcsolódik – így kerül el a két azonos captionű menüpont
   ütközése.
 4. **Hossz-szabály** (>60 karakter): ilyenkor szavakban vágja, legfeljebb 58
    karakterig, hogy olvasható maradjon.
5. **Ütközés**: ha az így kapott név a formon belül máshol is fennáll (másik
   menüpont vagy bármely más azonosító), a szerszág `1`, `2`, … számot ad.
   A szülő-kontextus és a számozás együtt garantálja, hogy a formon belül két
   menüpont ne kapja ugyanazt a nevet.

> **Megjegyzés**: a kézilag átnevezett, szép neveket (pl. `miExcelFajlBetoltese`,
> `TetelekOsszesen`, `miTORfunkciok`) is átírja, ha azok nem egyeznek meg a
> caption alapú nével (ez a "minden menüpontot átnevezzük" szabály). Ha egy
> meglévő szép nevet szeretnél megtartani, a riportot ellenőrizd, mielőtt
> `--apply`-t futtasz.

## Kódolás

- `.dfm`: az eredeti kódolás csomagjában írja vissza.
- `.pas`: UTF-8 (BOM-mal vagy BOM-nélkül) vagy cp1250 (magyar ANSI) detektálás;
  a kiírás ugyanabban a kódolásban történik, mint a beolvasás.
- **CRLF**: a sorvég-megőrzésen dolgozik, csak az azonosítókat cseréli, a
  sorvég-characterokat nem.

## Riport példa (rövid)

```
fájl: menu-rename/u_biz_tetel.dfm  (form: TAbl_biz_tetel)
  Mgsem1                  ->  miMegsem                 | caption: Mégsem
  Mgsem2                  ->  miMegsem1                | caption: Mégsem
  Sg1                     ->  miSugo                   | caption: Súgó
  Engedmny1               ->  miEngedmeny              | caption: Engedmény
  Kszlet1                 ->  miArEsKeszlet            | caption: Ár és készlet
  fakulcsmegads1          ->  miAfakulcsMegadas        | caption: Áfakulcs megadás
  sszenblegysgrszmts1     ->  miOsszenbolEgysegarSzamitasO | caption: Osszenbol egysegar szamitas (o)
  ...
  [handler] Engedmny1Click        -> miEngedmenyClick        (4 hivatkozás)
  [handler] fakulcsmegads1Click   -> miAfakulcsMegadasClick  (3 hivatkozás)
  (separatorok nem érintett: N1)

Összesen: X menüpont, Y handler, Z form.
```

A TSV soraiba: `u_biz_tetel.dfm<TAB>Mgsem1<TAB>miMegsem`. Így SQL-ben is
használd az adatbázis "kedvenc menüpontjai" táblájának frissítéséhez:

```sql
UPDATE kedvencmenu
SET nev = VALUES(nev)
FROM (
  VALUES ('Mgsem1','miMegsem'), ...
) AS m(old, new)
WHERE kedvencmenu.nev = m.old;
```

## Fájlstruktúra

```
menu-rename/
  menu_rename.py         – a szerszág
  README.md              – ez a fájl
  .gitignore             – tesztfájlok, riport, tsv, .bak
  u_biz_tetel.dfm/.pas   – gitignored (teszt)
  U_FORG.dfm/.PAS        – gitignored (teszt)
```

## Git

- A `menu-rename/` mappa git-repo; a `menu_rename.py` és `README.md` versionzódik.
- A szerszág nem commit-el; a commit-ot te teszed meg.
- A `.gitignore` kizárja a Delphi tesztfájlokat (`.pas`, `.dfm`), a riportokat
  (`menu_rename_report.txt`, `menu_rename_mapping.tsv`) és a `.bak*` mentéseket.
