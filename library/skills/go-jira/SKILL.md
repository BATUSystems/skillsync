---
name: go-jira
description: Neutralny przewodnik po go-jira CLI (Netflix) - instalacja, konfiguracja i uwierzytelnianie na Linux i Windows, komendy, edycja custom fields przez szablony, pola wieloliniowe i pułapki.
---

## Kiedy używać tego skill-a
Używaj, gdy zadanie dotyczy pracy z Jirą przez CLI `jira` (go-jira): instalacji, konfiguracji i logowania,
przeglądania, tworzenia i edycji issues, transitions, worklogów, ustawiania custom fields i pól wieloliniowych
oraz diagnozy błędów uwierzytelniania.

Dla **Jira Cloud** podstawowym klientem jest `acli` (skill [acli](../acli/SKILL.md)); `go-jira` służy do operacji, których `acli` nie obsługuje
(np. zapis pól własnych `customfield_*`) oraz do starszych instancji.

Wartości instancji i projektu (endpoint, klucz projektu, login, wymagane pola, ID custom fields):
firmowe konwencje w skillu organizacji, projektowe w skillu projektu.
W dalszej części `<KEY>-123` oznacza dowolny klucz issue, `<user>` login Jiry, `<endpoint>` adres instancji.

# go-jira CLI

**Narzędzie:** go-jira (Netflix), testowane na v1.0.27.

## Instalacja

```bash
# Arch Linux
yay -S go-jira                # AUR
```
```powershell
# Windows (Chocolatey)
choco install go-jira -y      # binarka: C:\ProgramData\chocolatey\lib\go-jira\tools\jira.exe
```

## Konfiguracja

### Linux

`~/.jira.d/config.yml`:
```yaml
endpoint: <endpoint>
user: <user>
password-source: gopass       # patrz "Hasło na Linuksie"
project: <KEY>
timezone: Europe/Warsaw
insecure: true                # tylko gdy serwer ma certyfikat self-signed
```

**Uwaga:** go-jira na Linuksie **nie obsługuje** `~/.netrc` (plik `_netrc` działa na Windows). Plain text hasło w `token:` nie
cachuje sesji i wymaga hasła przy każdej komendzie; użyj menedżera haseł (patrz "Hasło na Linuksie"). Sesja jest
cache'owana w `~/.jira.d/cookies.js`.

### Jira Cloud (Atlassian, np. `https://example.atlassian.net`)

Jira Cloud różni się od on-prem (kopię starej konfiguracji możesz zachować, np. `~/.jira.d/config.onprem.yml.bak`). Różnice względem on-prem:

```yaml
endpoint: https://example.atlassian.net
user: <e-mail>                          # e-mail konta Atlassian, nie login
authentication-method: api-token
password-source: gopass                 # sekret pod ścieżką GoJira/api-token:<e-mail> (NIE GoJira/<e-mail>)
project: <KEY>
timezone: Europe/Warsaw
# bez insecure: true - Cloud ma poprawny certyfikat
```

- **Token API:** id.atlassian.com → Security → API tokens (klasyczny, bez scopes, max 1 rok). Zapis:
  `gopass insert "GoJira/api-token:<e-mail>"`. Po zmianie instancji usuń `~/.jira.d/cookies.js`.
- **Zmienne środowiskowe** `JIRA_ENDPOINT`, `JIRA_USERNAME`, `JIRA_API_TOKEN` przesłaniają config - sprawdź, czy nie wskazują on-prem.
- **`jira ls` i `--query` NIE DZIAŁAJĄ** (go-jira 1.0.27 używa usuniętego `/rest/api/2/search`, błąd CHANGE-2046 / HTTP 410).
  Do wyszukiwania użyj `jira jql '<JQL>' [--limit N]` (komenda w config.yml, woła `jira-jql`) albo wprost `jira-jql '<JQL>' [limit] [pola]`.
  Skrypt: `scripts/jira-jql` w tym skillu (instalacja: `cp scripts/jira-jql ~/.local/bin/ && chmod +x`; wymaga `curl`, `jq`, `gopass`),
  używa `/rest/api/3/search/jql` ze stronicowaniem tokenowym. Komenda w `~/.jira.d/config.yml`:
  ```yaml
  custom-commands:
    - name: jql
      help: Wyszukiwanie JQL w Jira Cloud (zamiennik ls)
      args: [{name: JQL, required: true}]
      options: [{name: limit, short: l, type: int, default: 50}]
      script: |-
        exec "$HOME/.local/bin/jira-jql" '{{args.JQL}}' {{options.limit}}
  ```
- **Działają bez zmian:** `view`, `create`, `comment`, `transitions`, `transition`, `worklog`, `editmeta` (endpointy issue, API v2, wiki markup).
- **User picker i `assign`:** Cloud wymaga `accountId`, nie loginu. Szablony z `{name: <login>}` zamień na `{accountId: "<id>"}`
  (id użytkownika: `jira-jql`/REST `/rest/api/3/user/search?query=<e-mail>` albo pole `assignee` w `jira view --template json`).
- **ID custom fields i przejść są INNE niż on-prem**; mapa pól w skillu organizacji. Zawsze potwierdź przez `jira editmeta` i `jira transitions`.
- Szablony `~/.jira.d/templates/update-*` przepisz na Cloud (kopia on-prem np. `~/.jira.d/templates.onprem.bak`); nowe szablony pisz od razu z ID Cloud.
- Pełny test po konfiguracji: `jira view <KEY>-1` (auth) i `jira jql 'project = <KEY>' --limit 1` (wyszukiwanie).

### Windows

- Config: `%USERPROFILE%\.jira.d\config.yml` (te same klucze co wyżej, np. `password-source: pass`)
- Uwierzytelnianie: `%USERPROFILE%\_netrc` - **underscore**, nie `.netrc`!
  ```
  machine <host-jiry>
  login <user>
  password <hasło>
  ```

### Hasło na Linuksie (gopass)

go-jira na Linuksie nie cachuje sesji dla hasła podanego plain text, więc wymaga hasła przy każdej komendzie.
Rozwiązanie: `password-source: gopass`.

1. `sudo pacman -S gopass`
2. `gopass init <twój-email-gpg>`
3. Hasło na ŚCIEŻCE `GoJira/<user>` (`<user>` = wartość `user:` z config.yml):
   ```bash
   gopass insert GoJira/<user>
   gopass show GoJira/<user>      # weryfikacja
   ```
4. Wyczyść cache sesji i przetestuj:
   ```bash
   rm -f ~/.jira.d/cookies.js
   jira ls --limit 1
   ```

Inne źródła: `password-source: keyring` (system keyring), `pass` (legacy menedżer pass), `stdin` (pipelining).

## Podstawowe komendy

```bash
# Jira Cloud: `ls`/`--query` nie działają - użyj `jira jql '<JQL>'` (sekcja "Jira Cloud")
jira ls --project <KEY> --limit 10
jira ls --assignee <user>
jira ls --query "project = <KEY> AND status = 'In Progress'"

jira view <KEY>-123                       # (nie: jira issue view)
jira view <KEY>-123 --expand comment      # komentarze; flaga --comments bywa niewiarygodna
jira view <KEY>-123 --template json       # surowy JSON (pełne custom fields)

jira create --project <KEY> --issuetype Bug --summary "Tytuł" --priority Major
jira comment <KEY>-123 --message "Treść"   # kolory i struktura komentarzy: skill organizacji
jira assign <KEY>-123 <user>

jira transitions <KEY>-123                # lista dostępnych przejść
jira transition "In Progress" <KEY>-123

jira worklog add <KEY>-123 --time-spent 2h --comment "Opis pracy"
jira worklog list <KEY>-123
```

W skryptach używaj `--no-input` (bez interaktywnych promptów); przy `edit` w sesji nieinteraktywnej `--noedit`.

## Edycja custom fields przez szablon

Własny, minimalny szablon działa niezawodnie dla pól select, user picker i textarea/string (także polski tekst
z ogonkami). Nie używaj domyślnego `jira edit` z `-o pole=wartość` (patrz Pułapki).

### Krok 1: odkryj prawdziwe ID i typ pola przez `editmeta`

Nie zgaduj ID - `customfield_XXXXX` różni się między instancjami i projektami:

```bash
jira editmeta <KEY>-123 > /tmp/editmeta.json
grep -n -B2 '"name": "<Nazwa pola>"' /tmp/editmeta.json
```

Sekcja `schema.custom` pola mówi, jakiego kształtu YAML potrzebuje szablon:
- `...customfieldtypes:select` (`type: option`) → `{value: "Nazwa opcji"}` - dokładna wartość z `allowedValues` (np. "Low", nie "low").
- User picker (`autoCompleteUrl` z `users/picker`) → `{name: "login"}` (login, nie email ani display name).
- `...customfieldtypes:textarea` (`type: string`) → zwykły string lub blok YAML `|-`.
- Pola systemowe, np. `fixVersions` (`type: array`) → tablica obiektów `{id: "..."}`; ID wersji znajdziesz w
  `allowedValues` tego pola w `editmeta` (dopasuj po `name`).

### Krok 2: napisz WŁASNY, minimalny szablon

Ścieżka: `~/.jira.d/templates/<nazwa>` (Windows: `%USERPROFILE%\.jira.d\templates\<nazwa>`).
Zawiera WYŁĄCZNIE sekcję `fields:` z polami, które faktycznie zmieniasz:

```yaml
fields:
  fixVersions:
    - id: "<ID_WERSJI>"
  customfield_<ID_SELECT>:
    value: <WARTOŚĆ_OPCJI>
  customfield_<ID_USERA>:
    name: <login>
  customfield_<ID_TEXTAREA>: |-
    Pierwsza linia
    Druga linia (nowe linie są zachowane)
```

Użycie (zawsze z `--noedit`, żeby nie otwierać edytora w sesji nieinteraktywnej):
```bash
jira edit <KEY>-123 --template <nazwa> --noedit
```

Ten sam szablon można reużywać między ticketami - podmień wartości przed kolejnym `jira edit`.

### Krok 3: zweryfikuj surowym JSON-em

Tekstowy `jira view` nie pokazuje wszystkich custom fields:

```bash
jira view <KEY>-123 --template json | python3 -c "
import json,sys
f = json.load(sys.stdin)['fields']
print(f.get('customfield_<ID>'))
"
```

## Pułapki i troubleshooting

### Domyślny szablon `edit` wywala błąd niezwiązany z ustawianym polem
**Objaw:** `ERROR priority: Nazwa priorytetu "..." jest niepoprawna. assignee: expected Object containing a 'name' property`.  
**Przyczyna:** domyślny szablon `edit` odsyła CAŁE istniejące issue w PUT (summary, assignee, priority, reporter,
description). Na zlokalizowanych instancjach `priority.name` bywa spolonizowaną etykietą (np. "Mniejszy" zamiast
"Minor"), której API nie przyjmuje z powrotem, a fallback dla `assignee` buduje zły kształt obiektu.  
**Rozwiązanie:** własny minimalny szablon z samym `fields:` i `jira edit ... --template ... --noedit`.

### Nowe linie w polach wieloliniowych giną
Użyj `--noedit`, blok YAML `|-` w szablonie i weryfikuj przez `jira view --template json`. Potwierdzone dla pól textarea.

### Markup `{{kod}}` w szablonie: `function "X" not defined`
**Objaw:** `jira edit ... --template <nazwa>` kończy się `ERROR Invalid Usage: template: gojira:<linia>: function "X" not defined`
i niczego nie zapisuje.  
**Przyczyna:** plik `--template` jest szablonem Go (text/template), więc inline code Jira `{{X.pole}}`
jest interpretowany jako akcja szablonu.  
**Rozwiązanie:** każde `{{X}}` zapisz jako literał Go `{{"{{X}}"}}` (do Jiry trafia `{{X}}`); weryfikuj przez
`jira view --template json`. Potwierdzone na Jira Cloud.

### Szablon nie aktualizuje custom field
Zwykle zły kształt YAML (sprawdź `schema.custom` w `editmeta`: `value:` dla select, `name:` dla user picker, string
dla textarea). Gdy kształt jest poprawny, a pole nadal nie działa (cascading select, multi-user picker, pola
z zależnościami) - fallback: interfejs web.

### Tablice przez web
`fixVersions` i inne tablice potrafią sprawiać kłopot w CLI (zależnie od instancji i wersji). Jeśli szablon z
`- id: "..."` nie zadziała, ustaw je przez interfejs web albo REST.

### `--insecure`
Przy certyfikacie self-signed ustaw `insecure: true` w config.yml (albo flagę `--insecure`). Używaj tylko dla zaufanych
instancji wewnętrznych.

### Błąd uwierzytelniania
- Windows: sprawdź `_netrc` (underscore!).
- Linux: sprawdź `password-source: gopass` w config.yml i `gopass show GoJira/<user>` (Cloud z `authentication-method: api-token`: `GoJira/api-token:<e-mail>`); wyczyść `~/.jira.d/cookies.js`.
- `Error: failed to retrieve secret "GoJira/<user>": entry is not in the password store`: sprawdź `gopass list | grep GoJira`,
  dodaj wpis ponownie (`gopass insert -f GoJira/<user>`), potwierdź `user:` w config.yml.
- `WARNING pass binary was not found!`: przy `password-source: pass` brak binarki `pass`, więc jira pyta o hasło w terminalu.

### Auth działa w interaktywnym terminalu, ale nie w sesji agenta
**Objaw:** w terminalu użytkownika `jira view` działa, a z narzędzia agenta kończy się:
```
gpg: public key decryption failed: No secret key
Error: failed to retrieve secret "GoJira/<user>": failed to decrypt
```
**Przyczyna:** gopass odszyfrowuje przez GPG, co może wymagać pinentry/klucza dostępnego tylko w interaktywnej sesji
desktopowej; proces w tle może nie mieć dostępu do tego samego gpg-agent.  
**Rozwiązanie:** sprawdź świeżo na początku sesji `jira view <znany-issue>` - stan zmienia się między sesjami
(po użyciu `jira` w interaktywnym terminalu użytkownika auth zaczyna działać także dla agenta; obserwowane).
Jeśli nadal nie działa, poproś użytkownika o wklejenie treści ticketu albo o uruchomienie `jira` we własnym terminalu.

## Powiązane Skills

- skill organizacji - firmowa instancja Jira i wspólne konwencje pól (wymagane pola, ID custom fields)
- skill projektu - wartości projektu (konfiguracja, przykłady pól)
- `azure-devops` - powiązane narzędzie CLI
