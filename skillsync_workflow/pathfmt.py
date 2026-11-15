"""Ścieżki w polach raportów CLI: zawsze z ukośnikiem `/` (WIN-141).

Raport JSON jest kontraktem dla skryptów, skilli i wtyczki edytora; ta sama ścieżka ma na każdym systemie tę samą postać
(`D:/work/docs/README.md`, a nie `D:\\work\\docs\\README.md`). Ścieżki na dysku się nie zmieniają, zmienia się tylko
zapis w polu raportu. Polecenia do wykonania (np. generowane przez `selfcmd`) NIE przechodzą przez ten pomocnik: powłoka
potrzebuje formy natywnej."""
import os


def posix(value):
    """`value` (ścieżka albo tekst ze ścieżką) z `/` zamiast `\\`; na Linuksie i macOS bez zmian (tam `\\` jest częścią nazwy)."""
    text = str(value)
    return text.replace('\\', '/') if os.name == 'nt' else text
