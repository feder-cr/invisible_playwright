#!/usr/bin/env python3
"""CI font gate - assert the patched binary exposes exactly the Windows font
persona on EVERY host OS (Windows / Linux / macOS), with zero host-font leak.

The patched binary is bundle-only: at font-list construction it drops every host
system font and exposes only the bundled Windows-11 family set (the exposed set
IS the bundle). This gate launches the binary on its NATIVE runner - so
macOS/CoreText, Linux/fontconfig and Windows/DWrite are each tested for real -
enumerates the visible families with the same width-probe web detectors use, and
asserts three things:

  1. the detected family set == the canonical Windows set (EXPECTED): the SAME
     set on all three platforms. A leaked host font or a missing Windows one
     fails here. This is the "identical on every OS" contract.
  2. no known host family is visible (macOS: Helvetica Neue / Geneva / Menlo ...;
     Linux: DejaVu / Ubuntu ...) - a POSITIVE proof that block-at-birth ran for
     this platform's backend, not just "no obvious tell".
  3. the CSS generics resolve to Windows fonts (serif=Times New Roman,
     sans-serif=Arial, monospace=Consolas) and system-ui=Segoe UI.

This is the macOS validator the local Win/Linux gate cannot be - there is no
local Mac, so CoreText is only ever exercised here. Headless, no proxy, no
secrets, loopback-free (about:blank + arrow-function evaluate, which is not
eval and carries no CSP problem) -> safe in public CI.

Usage:  python ci_font_gate.py <firefox-binary>
Exit 0 + "FONT GATE OK ..." on success; non-zero + the diff on failure.
"""
from __future__ import annotations

import sys

# The canonical Windows-11 family set the bundle exposes. Verified byte-for-byte
# identical on Windows/DWrite and Linux/fontconfig; macOS/CoreText must match it
# too. Questi sono i record `F|` che `invisible_core` dichiara.
#
# ⛔ E' UN LETTERALE, e il motivo NON e' pigrizia: verificato il 2026-08-17
# leggendo il workflow che lo esegue. Il font gate gira dentro `release.yml`
# del repo SORGENTE, in un job che fa il checkout di QUESTO repo ma installa
# soltanto `playwright==...` e niente altro. Farlo derivare da `invisible_core`
# e' la forma che la regola 16 chiede, e' stata scritta e provata, funziona in
# locale, e fa morire di ImportError OGNI rilascio.
#
# E c'e' una seconda ragione, che sopravvive anche se un giorno quel job
# installasse il core: quando la pipeline gira, il core PUBBLICATO e' ancora
# quello del rilascio precedente, perche' il core si pubblica DOPO che il
# binario esiste (17-release-seal-spec.md §9). Derivare da li' misurerebbe il
# binario nuovo contro la dichiarazione vecchia.
#
# Cio' che rende sicuro il letterale non e' l'attenzione di chi lo edita: e'
# `test_the_family_list_in_the_gate_matches_the_core_manifest`, in
# `tests/test_ci_font_gate_declaration.py` di questo repo, dove il core C'E'.
# Quel test non esisteva: era DOCUMENTATO come esistente e basta, ed e'
# esattamente per questo che la lista ha potuto restare a 68 mentre il manifest
# passava a 71 (le due Segoe di icone piu' Twemoji Mozilla).
#
# ⛔ E LA DERIVA NON DA' UN ROSSO, DA' UN VERDE: misurato il 2026-08-17 sul
# binario vero. La sonda interroga `EXPECTED + HOST_MUST_BE_ABSENT` e nient'
# altro, quindi una famiglia tolta di qui smette anche di essere CERCATA: con
# le tre mancanti il gate ha stampato `detected 68 families (expected 68)` e
# `FONT GATE OK`, uscita 0, su un binario che ne espone 71. Un gate d'accordo
# con se' stesso non vede la propria deriva.
EXPECTED = [
    "Arial", "Bahnschrift", "Calibri", "Cambria", "Cambria Math", "Candara",
    "Comic Sans MS", "Consolas", "Constantia", "Corbel", "Courier New",
    "Ebrima", "Franklin Gothic", "Gabriola", "Gadugi", "Georgia", "Impact",
    "Ink Free", "Javanese Text", "Leelawadee", "Leelawadee UI",
    "Lucida Console", "Lucida Sans Unicode", "MS Gothic", "MS PGothic",
    "MS UI Gothic", "MV Boli", "Malgun Gothic", "Marlett",
    "Microsoft Himalaya", "Microsoft JhengHei", "Microsoft JhengHei UI",
    "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Sans Serif",
    "Microsoft Tai Le", "Microsoft Uighur", "Microsoft YaHei",
    "Microsoft YaHei UI", "Microsoft Yi Baiti", "MingLiU-ExtB",
    "Mongolian Baiti", "Myanmar Text", "NSimSun", "Nirmala UI",
    "PMingLiU-ExtB", "Palatino Linotype", "Segoe Fluent Icons",
    "Segoe MDL2 Assets", "Segoe Print", "Segoe Script", "Segoe UI",
    "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol", "SimSun",
    "SimSun-ExtB", "Sitka Small", "Sylfaen", "Symbol", "Tahoma",
    "Times New Roman", "Trebuchet MS", "Twemoji Mozilla", "Verdana",
    "Webdings", "Wingdings", "Wingdings 2", "Wingdings 3", "Yu Gothic",
    "Yu Gothic UI",
]

# Host families that must NEVER be visible - one per backend. Their presence is a
# hard fail (block-at-birth did not run for this OS). These are decoys added to
# the probe list; they must all come back absent.
HOST_MUST_BE_ABSENT = [
    # macOS / CoreText
    "Helvetica Neue", "Geneva", "Menlo", "Monaco", "Avenir", "Lucida Grande",
    "Apple SD Gothic Neo", "PingFang SC",
    # Linux / fontconfig
    "DejaVu Sans", "Liberation Sans", "Ubuntu", "Nimbus Sans", "Noto Sans",
    # Office / non-standard families intentionally dropped from the bundle
    "Century Gothic", "Agency FB", "Monotype Corsiva", "Pristina",
]

# CSS generic -> the Windows family it must resolve to under bundle-only.
GENERICS = {
    "serif": "Times New Roman",
    "sans-serif": "Arial",
    "monospace": "Consolas",
    "system-ui": "Segoe UI",
}

# Families that live inside a .ttc TrueType Collection (several faces packed in
# one file). Being *listed* is not enough: without a per-face index the table
# lookup reads the first font of the collection, so every other face silently
# falls back to a default one. That is invisible to the presence probe below
# (the family is registered either way) but wrecks the persona for CJK text.
# The FF150->151 rebase dropped exactly that fix and only 7 of these loaded.
#
# Calibrated on firefox-17, the known-good release: these are the families that
# demonstrably render the CJK sample with their own face there. Deliberately
# NOT listed: "SimSun-ExtB" (covers Unicode Ext-B, not the BMP characters in
# the sample, so it legitimately falls back) and the "... UI" variants of YaHei
# and JhengHei (they already fall back on firefox-17, so requiring them would
# fail the known-good build). Keep this list as a regression detector, not an
# aspiration: everything here loads on firefox-17 and must keep loading.
#: Un seme fisso, perche' le metriche dichiarate dipendono dal profilo e un
#: gate che cambia numeri a ogni giro non e' un gate.
_SEED = 970411

#: COPIA di zoom.stealth.fonts.generics come lo dichiara invisible_core.
#: Costante fra i semi (verificato su 5). Legata al core dal test
#: test_ci_font_gate_generics_match_the_core, che diventa rosso se divergono.
GENERICS_DECL = (
    "cursive||Comic Sans MS\n"
    "serif|x-math|Cambria Math\n"
    "sans-serif|ja|Yu Gothic UI\n"
    "serif|ja|Yu Gothic UI\n"
    "monospace|ja|Yu Gothic UI\n"
    "sans-serif|ko|Malgun Gothic\n"
    "serif|ko|Malgun Gothic\n"
    "monospace|ko|Malgun Gothic\n"
    "sans-serif|zh-CN|Microsoft YaHei UI\n"
    "serif|zh-CN|Microsoft YaHei UI\n"
    "monospace|zh-CN|Microsoft YaHei UI\n"
    "sans-serif|zh-TW|Microsoft JhengHei UI\n"
    "serif|zh-TW|Microsoft JhengHei UI\n"
    "monospace|zh-TW|Microsoft JhengHei UI\n"
    "sans-serif|zh-HK|Microsoft JhengHei UI\n"
    "serif|zh-HK|Microsoft JhengHei UI\n"
    "monospace|zh-HK|Microsoft JhengHei UI\n"
    "serif||Times New Roman\n"
    "sans-serif||Arial\n"
    "monospace||Consolas"
)



TTC_FAMILIES = [
    "Microsoft YaHei", "Microsoft YaHei UI",
    "Microsoft JhengHei", "Microsoft JhengHei UI",
    "MS Gothic", "MS PGothic", "MS UI Gothic",
    "SimSun", "NSimSun",
    "Yu Gothic", "Yu Gothic UI", "Malgun Gothic",
    "MingLiU-ExtB", "PMingLiU-ExtB",
]

#: Le famiglie qui sopra raggruppate per FACCIA, misurate dalla scatola
#: d'inchiostro. E' l'invariante su cui poggia il controllo, e dice due cose in
#: una: che ogni faccia carica (una faccia che non carica cade nel gruppo del
#: ripiego, e il raggruppamento cambia) e che le due piattaforme rispondono
#: uguale (i valori sono identici, non solo i gruppi).
#:
#: Il gruppo del RIPIEGO contiene YaHei perche' YaHei E' la faccia di ripiego
#: per il CJK - misurato ai pixel: chiedendo un font inesistente si ottengono
#: esattamente i suoi glifi. MingLiU-ExtB e PMingLiU-ExtB stanno li' per un
#: motivo diverso e altrettanto corretto: sono font dell'Estensione B e la loro
#: cmap NON copre nessuno dei caratteri della sonda, quindi il ripiego disegna
#: come deve. Il vecchio controllo chiedeva "questa famiglia misura diverso da
#: NESSUN font?" e su queste quattro rispondeva no, deducendone che la faccia
#: non fosse caricata: la domanda era mal posta, perche' la faccia di ripiego e'
#: essa stessa una delle famiglie imbarcate.
EXPECTED_FACE_GROUPS = [
    # Il gruppo del RIPIEGO. Contiene YaHei perche' YaHei E' la faccia di
    # ripiego per il CJK, misurato ai pixel: chiedendo un font inesistente si
    # ottengono esattamente i suoi glifi. MingLiU-ExtB e PMingLiU-ExtB stanno
    # qui per un motivo diverso e altrettanto corretto: sono facce
    # dell'Estensione B e la loro cmap non copre NESSUNO dei caratteri della
    # sonda (verificato leggendo la cmap dei file imbarcati), quindi il ripiego
    # disegna come deve.
    {"Microsoft YaHei", "Microsoft YaHei UI", "MingLiU-ExtB",
     "PMingLiU-ExtB", "__NoSuchFontXYZ__"},
    {"Microsoft JhengHei", "Microsoft JhengHei UI"},
    {"SimSun", "NSimSun"},
    {"MS Gothic"},
    {"MS PGothic"},
    {"MS UI Gothic"},
    {"Yu Gothic"},
    {"Yu Gothic UI"},
    {"Malgun Gothic"},
]

# Width+height probe (the offsetWidth method real detectors use): a family is
# "present" if styling text in it renders at a different size than the three CSS
# base generics. For the generics, return the measured size of each generic and
# of its target Windows family so the caller can assert they coincide.
DETECT_JS = r"""(arg) => {
  const bases = ['monospace', 'sans-serif', 'serif'];
  const sample = 'mmmmmmmmmmlli WwQ 0123456789 gjpqy';
  const sp = document.createElement('span');
  sp.style.cssText =
    'position:absolute;left:-9999px;font-size:72px;white-space:nowrap;';
  sp.textContent = sample;
  document.body.appendChild(sp);
  const size = (ff) => { sp.style.fontFamily = ff; return sp.offsetWidth + 'x' + sp.offsetHeight; };
  const bw = {};
  for (const b of bases) bw[b] = size(b);
  const present = {};
  for (const f of arg.cands) {
    present[f] = bases.some((b) => size("'" + f + "'," + b) !== bw[b]);
  }
  const gen = {};
  for (const g of arg.generics) gen[g] = size(g);
  const genref = {};
  for (const w of arg.targets) genref[w] = size("'" + w + "'");
  // La scatola d'INCHIOSTRO di measureText per una stringa CJK, famiglia per
  // famiglia. Non i pixel e non l'altezza di riga, per due ragioni misurate:
  //   - l'altezza di riga e' un valore che DICHIARIAMO noi, quindi famiglie
  //     diverse possono legittimamente condividerla e il confronto non
  //     distingue piu' niente;
  //   - i pixel di un canvas con testo non sono leggibili su Linux (voce
  //     2026-08-11 in 70-known-bugs.md).
  // La scatola d'inchiostro e' geometria derivata dai glifi: misurata
  // 2026-08-11 e' identica alla terza cifra fra Windows e Linux.
  const c = document.createElement('canvas');
  const cx = c.getContext('2d');
  const inkbox = {};
  for (const f of arg.ttc.concat(['__NoSuchFontXYZ__'])) {
    cx.font = "56px '" + f + "', '__NoSuchFontXYZ__'";
    const m = cx.measureText(arg.cjk);
    inkbox[f] = [m.width, m.actualBoundingBoxAscent, m.actualBoundingBoxDescent,
                 m.actualBoundingBoxLeft, m.actualBoundingBoxRight]
                .map((v) => Math.round(v * 1000) / 1000).join('|');
  }
  document.body.removeChild(sp);
  return { present, gen, genref, inkbox };
}"""

# Suppress the new-tab machinery so the launch is quiet (mirrors ci_drive_gate).
_PREFS = {
    "browser.startup.page": 0,
    "browser.newtabpage.enabled": False,
    "browser.newtab.preload": False,
    "browser.newtabpage.activity-stream.enabled": False,
}


# ── Il limite strutturale di questo gate, misurato e non dedotto ──────────
#
# Una pagina non puo' ENUMERARE i font di sistema: puo' solo chiedere nome per
# nome. Quindi la sonda interroga `EXPECTED + HOST_MUST_BE_ABSENT` e nient'altro,
# e da qui segue una cosa che va detta invece che scoperta: **una famiglia tolta
# da EXPECTED smette anche di essere CERCATA**, quindi il conteggio torna e il
# gate passa. Verificato 2026-08-11 come mutazione: cancellando "Georgia"
# dall'elenco il gate ha risposto "detected 67 families (expected 67)" e OK.
#
# Non e' correggibile dall'interno: e' il motivo per cui l'elenco esiste. La
# conseguenza pratica e' che HOST_MUST_BE_ABSENT deve restare GENEROSO, perche'
# e' l'unico posto da cui puo' arrivare la scoperta di un font dell'host che non
# ci aspettavamo. Un nome che non e' in nessuna delle due liste e' invisibile a
# questo gate per costruzione.


def main(exe: str) -> int:

    cands = EXPECTED + HOST_MUST_BE_ABSENT
    arg = {
        "cands": cands,
        "generics": list(GENERICS.keys()),
        "targets": list(GENERICS.values()),
        "ttc": TTC_FAMILIES,
        "cjk": "中文字体測試あア漢字",
    }
    # Lancio GREZZO, e una dichiarazione consegnata a mano. Il job di gate in CI
    # installa solo Playwright: invisible_core non c'e' e non puo' esserci senza
    # cambiare il workflow, cioe' senza ricostruire i cinque archivi.
    #
    # La sola cosa che manca a un motore lanciato grezzo e' la mappa dei
    # generici. Misurato 2026-08-11 sullo stesso binario: senza,
    # serif/sans-serif/monospace/cursive/fantasy collassano TUTTI su Arial su
    # Linux (su Windows no, perche' li' i default di Gecko coincidono per caso
    # con la persona che dichiariamo); con, mappano su Times New Roman, Arial,
    # Consolas e Comic Sans su ENTRAMBE, con gli stessi numeri.
    #
    # GENERICS_DECL sotto e' una COPIA di cio' che invisible_core dichiara, ed
    # e' l'unica del progetto. Non puo' divergere in silenzio: la lega il test
    # test_ci_font_gate_generics_match_the_core, che confronta questa stringa
    # con quella prodotta dal core e diventa rosso se si separano.
    from playwright.sync_api import sync_playwright

    prefs = dict(_PREFS)
    prefs["zoom.stealth.fonts.generics"] = GENERICS_DECL
    with sync_playwright() as p:
        browser = p.firefox.launch(executable_path=exe, headless=True,
                                   firefox_user_prefs=prefs)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            r = page.evaluate(DETECT_JS, arg)
        finally:
            browser.close()

    detected = {f for f, v in r["present"].items() if v}
    expected = set(EXPECTED)
    missing = sorted(expected - detected)
    # Anything detected that isn't in EXPECTED (host leaks land here too).
    extra = sorted(detected - expected)
    leaked_host = [h for h in HOST_MUST_BE_ABSENT if r["present"].get(h)]
    gen_bad = []
    for g, want in GENERICS.items():
        got, ref = r["gen"].get(g), r["genref"].get(want)
        if got != ref:
            gen_bad.append(f"{g} -> {got} (expected {want} = {ref})")

    n = len(detected)
    print(f"[font-gate] {exe}")
    print(f"[font-gate] detected {n} families (expected {len(EXPECTED)})")
    if missing:
        print(f"[font-gate] MISSING (in bundle, not exposed): {missing}")
    if extra:
        print(f"[font-gate] UNEXPECTED (exposed, not in canonical set): {extra}")
    if leaked_host:
        print(f"[font-gate] HOST LEAK (block-at-birth did not run!): {leaked_host}")
    if gen_bad:
        print(f"[font-gate] GENERIC MISMATCH: {gen_bad}")
    # I gruppi di facce, misurati adesso, contro quelli attesi. Un raggruppamento
    # che cambia dice che una faccia e' caduta nel ripiego; un VALORE che cambia
    # dice che le metriche dichiarate si sono mosse, o che le due piattaforme non
    # rispondono piu' uguale. Sono due difetti diversi e il messaggio li separa.
    ink = r.get("inkbox", {})
    visti = {}
    for fam, box in ink.items():
        visti.setdefault(box, []).append(fam)
    # Si confronta la STRUTTURA - quali famiglie condividono una faccia - non i
    # valori. I valori sono identici fra Windows e Linux solo quando il browser
    # riceve tutte le sue dichiarazioni, e questo gate lo lancia grezzo perche'
    # il job della CI non ha invisible_core: a lancio grezzo Linux torna bounds
    # interi (la grid-fit di FreeType) mentre Windows torna frazioni. La
    # struttura invece coincide, e' quella che risponde alla domanda "questa
    # faccia ha caricato?" - una faccia che non carica CADE nel gruppo del
    # ripiego e il raggruppamento cambia. La parita' cross-OS dei valori e' un
    # controllo diverso, che va fatto sotto il wrapper dove le dichiarazioni ci
    # sono tutte.
    misurati = [set(f) for f in visti.values()]
    face_bad = []
    for atteso in EXPECTED_FACE_GROUPS:
        if atteso not in misurati:
            vicino = max(misurati, key=lambda g: len(g & atteso), default=set())
            face_bad.append(f"gruppo atteso {sorted(atteso)} non trovato; "
                            f"il piu' vicino misurato e' {sorted(vicino)}")
    for g in misurati:
        if g not in EXPECTED_FACE_GROUPS:
            face_bad.append(f"gruppo non previsto: {sorted(g)}")
    if face_bad:
        print("[font-gate] FACCE: il raggruppamento non e' quello atteso")
        for riga in face_bad:
            print(f"[font-gate]   {riga}")

    ok = (not missing and not extra and not leaked_host and not gen_bad
          and not face_bad)
    if ok:
        print(f"FONT GATE OK - exactly the {n} Windows families, host-leak 0, "
              f"generics map to Windows (serif/sans/mono/system-ui), "
              f"{len(EXPECTED_FACE_GROUPS)} face groups over "
              f"{len(TTC_FAMILIES)} CJK families (every declared face draws "
              f"its own glyphs).")
        return 0
    print("FONT GATE FAILED - the exposed set does not match the Windows "
          "persona on this OS (see the diff above).")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python ci_font_gate.py <firefox-binary>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
