"""I due file e2e della PUBBLICAZIONE non lasciano passare l'ambiente nel venv.

**Perche' questo file esiste.** `test_release_e2e.py` e `test_upgrade_e2e.py`
creano un venv e ci installano il pacchetto DALL'INDICE: sono l'unica cosa che
verifica cosa riceve un utente. Ma un venv EREDITA l'ambiente, e fino al
2026-08-14 `subprocess.run(env=None)` glielo passava intatto. Misurato il
2026-08-11: `PYTHONPATH` verso i sorgenti del banco e `INVISIBLE_SEAL_FILE`
verso un sigillo locale hanno prodotto **sedici fallimenti in un giorno, nessuno
del prodotto**. E il verso pericoloso e' l'altro: un VERDE che arriva da un
ambiente che nessun utente ha.

**Perche' e' un test e non un commento.** Il rimedio vive in DUE copie, una per
file, e la duplicazione e' imposta da un gate del core
(`test_no_install_e2e_file_imports_a_package_the_runner_does_not_have`, in
`invisible_core/tests/test_marker_vocabulary.py`): quei file devono raccogliersi
con solo stdlib e pytest sul runner, quindi un modulo condiviso sarebbe un
errore di raccolta. Due copie divergono, a meno che qualcosa non le confronti.

Il caso che conta e' l'ULTIMO: il controllo che dimostra che senza la correzione
la variabile passerebbe davvero. Un test che verifica solo la versione corretta
non distingue "il rimedio funziona" da "il problema non esisteva".
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

QUI = Path(__file__).resolve().parent
FILE_E2E = ("test_release_e2e.py", "test_upgrade_e2e.py")

#: Le variabili misurate, piu' quella aggiunta per costruzione. Se un file
#: dimenticasse una di queste, il confronto fra le due copie qui sotto lo
#: riporterebbe come divergenza.
ATTESE = ("PYTHONPATH", "INVISIBLE_SEAL_FILE", "PYTHONHOME")


def _carica(nome: str):
    """Importa il modulo e2e dal PERCORSO, non dal nome.

    Il nome dipenderebbe da come pytest ha popolato `sys.path`, e questo test
    deve valere anche fuori da una corsa che raccoglie quei file.
    """
    percorso = QUI / nome
    spec = importlib.util.spec_from_file_location(nome[:-3] + "_letto", percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def moduli():
    return {n: _carica(n) for n in FILE_E2E}


def test_entrambi_i_file_dichiarano_le_stesse_variabili(moduli):
    """Due copie che divergono sono peggio di una copia sola."""
    visto = {n: tuple(m._NON_ERMETICHE) for n, m in moduli.items()}
    valori = set(visto.values())
    assert len(valori) == 1, (
        "i due file e2e ripuliscono ambienti DIVERSI, quindi uno dei due lascia "
        "passare qualcosa che l'altro ferma:\n  "
        + "\n  ".join("%s -> %s" % (n, v) for n, v in sorted(visto.items())))
    assert valori.pop() == ATTESE, (
        "l'elenco e' cambiato senza aggiornare questo test. Le tre attese sono "
        "%s: le prime due sono misurate (sedici rossi il 2026-08-11), la terza "
        "e' PYTHONHOME, che redirige la libreria standard e romperebbe il venv "
        "prima che esegua una riga." % (ATTESE,))


@pytest.mark.parametrize("nome", FILE_E2E)
def test_clean_env_toglie_le_variabili_e_lascia_le_altre(moduli, nome, monkeypatch):
    m = moduli[nome]
    monkeypatch.setenv("PYTHONPATH", "/c/src/firefox-stealth/release/invisible_core/src")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", "C:/tmp/seal-locale.json")
    monkeypatch.setenv("PYTHONHOME", "/opt/altrove")
    monkeypatch.setenv("UNA_QUALSIASI", "resta")

    env, tolte = m._clean_env()

    for k in ATTESE:
        assert k not in env, "%s: %s e' ancora nell'ambiente del sottoprocesso" % (nome, k)
    assert sorted(tolte) == sorted(ATTESE), (
        "%s: dice di aver tolto %s" % (nome, tolte))
    assert env.get("UNA_QUALSIASI") == "resta", (
        "%s: ha ripulito piu' del dovuto. Un ambiente svuotato rompe cose che "
        "non c'entrano - PATH, TEMP, le variabili del proxy - e la correzione "
        "diventa un difetto nuovo." % nome)


@pytest.mark.parametrize("nome", FILE_E2E)
def test_il_sottoprocesso_non_le_vede_davvero(moduli, nome, monkeypatch):
    """Il percorso VERO: non `_clean_env` in isolamento, ma `_run`.

    E' la differenza fra provare l'aiutante e provare cio' che il file fa. Il
    difetto originale non era in un aiutante: era `env=None` nella chiamata.
    """
    m = moduli[nome]
    monkeypatch.setenv("PYTHONPATH", "/percorso/del/banco")
    monkeypatch.setenv("INVISIBLE_SEAL_FILE", "C:/tmp/seal-locale.json")

    script = ("import os;"
              "print(os.environ.get('PYTHONPATH'), os.environ.get('INVISIBLE_SEAL_FILE'))")
    out = m._run([sys.executable, "-c", script], timeout=60).stdout.strip()
    assert out == "None None", (
        "%s: il sottoprocesso lanciato da _run vede ancora l'ambiente del "
        "chiamante: %r" % (nome, out))


def test_controllo_senza_il_rimedio_la_variabile_PASSEREBBE(monkeypatch):
    """L'input noto-cattivo, che qui e' il MONDO PRIMA della correzione.

    Senza questo, i tre test sopra non distinguono "il rimedio funziona" da "il
    problema non esisteva". Riproduce la chiamata com'era - `env=None` - e
    pretende che la variabile arrivi a destinazione.
    """
    monkeypatch.setenv("PYTHONPATH", "/percorso/del/banco")
    script = "import os; print(os.environ.get('PYTHONPATH'))"
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, timeout=60, env=None)
    assert r.stdout.strip() == "/percorso/del/banco", (
        "il controllo non riproduce il difetto: con env=None la variabile "
        "dovrebbe passare, e non e' passata. Allora i test qui sopra non stanno "
        "dimostrando quello che sembrano dimostrare, ed e' il BANCO a essere "
        "rotto - non il rimedio a funzionare. Ottenuto: %r" % r.stdout)
