# Publikacja: prywatna strona w hoście (Artifact)

Droga opcjonalna: tylko na życzenie użytkownika albo za jego zgodą na propozycję (np. gdy odbiorca nie ma dostępu do repo).
**Bez decyzji użytkownika niczego nie publikuj.** Droga dotyczy hosta, który ma narzędzie Artifact; w sesji bez niego
użyj [publish-static.md](publish-static.md) i nie zakładaj, że Artifact istnieje.

## Kontrakt (z dokumentacji hosta, nie z pamięci)

Przed pierwszą publikacją sprawdź w dokumentacji narzędzia bieżący kształt wywołania; poniżej tylko to, co ma znaczenie dla metody.

- Strona jest **prywatna**: widzi ją właściciel konta. Odbiorca zobaczy ją dopiero po **udostępnieniu przez właściciela**
  (przycisk udostępniania w hoście). Udostępnianiem zarządza właściciel, nie agent.
- Treścią jest jeden samodzielny plik HTML (z `assets/template.html`).
- Obrazy przekazujesz jako **pliki publikowane obok strony** (parametr `files`), nie jako `data:` w HTML. Ścieżki w HTML
  względne i zgodne z nazwami przekazanych plików.
- **Ta sama ścieżka pliku = ten sam adres.** Aktualizacja strony to ponowna publikacja pod tą samą ścieżką.

## Kroki

1. Złóż stronę i obrazy tak samo jak dla drogi statycznej; sprawdź lokalnie (oba motywy, 390 px).
2. Zapytaj użytkownika o zgodę na publikację, jeśli nie padła wprost.
3. Opublikuj plik HTML razem z obrazami w parametrze `files`.
4. Przekaż użytkownikowi adres z odpowiedzi narzędzia i powiedz: odbiorca zobaczy stronę dopiero po udostępnieniu
   przez właściciela; do tego czasu jest prywatna.
5. Adresu strony nie zapisuj do plików rdzenia ani publicznych dokumentów; jeśli projekt chce go mieć, zapisz tylko
   w jego własnej dokumentacji, za zgodą użytkownika.

## Aktualizacja

Po zmianie reguł opublikuj ponownie pod **tą samą ścieżką pliku**, żeby adres się nie zmienił; podaj w nagłówku nową
datę i wersję.

## Ograniczenia

Droga nie ma automatycznego testu. Zweryfikuj ją ręcznie (publikacja prywatna i obejrzenie wyniku) albo zgłoś jako niesprawdzoną.
