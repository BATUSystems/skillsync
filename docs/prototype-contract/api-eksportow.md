# API eksportów silnika prototypów

Zbiór nazw eksportowanych przez silnik w Node (`module.exports`) jest kontraktem: silnik eksportuje dokładnie te 106 nazw.
Opis podaje wejście i wynik słowami; szczegóły zachowania rozstrzygają testy z kolumny „Testy” i `comments-format.md` (format i fold).
Zbiór nazw (dokładnie 106) sprawdza `EngineV2ExportContractTests.test_export_names_are_exactly_the_contract_set`; każda nazwa ma test z wywołaniem i asercją wartości albo wyniku.
Kolumna „Testy” podaje `klasa.metoda` z `tests/test_skillsync_prototype_engine.py`.
Funkcje `filterThreads`, `filterCounts` i `threadMatches` przyjmują w kontekście dodatkowo `viewFilter` (iloczyn z filtrem statusu; test `EngineV2ExportContractTests.test_view_filter_in_context_multiplies_with_status_filter`).
Liczba w kolumnie „Argumenty” to długość deklarowanej listy parametrów; testy dopuszczają parametry opcjonalne.

| Nazwa | Rodzaj | Argumenty | Kontrakt | Testy |
|---|---|---|---|---|
| `ENGINE_VERSION` | number |  | wersja silnika: 2 | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `FORMAT_NAME` | string |  | nazwa formatu dziennika: `skillsync-prototype-comments` | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `FORMAT_VERSION` | number |  | wersja formatu dziennika: 2 | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `API_ROOT` | string |  | prefiks API serwera: `/_api/v1` | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `QUEUE_PREFIX` | string |  | prefiks klucza `localStorage` kolejki: `skillsync-prototype-queue:v2:` (dalej `<slug>:<login>`) | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `REJECTED_PREFIX` | string |  | prefiks klucza odrzuconych wpisów: `skillsync-prototype-rejected:v2:` | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `SEEN_PREFIX` | string |  | prefiks klucza ostatniej wizyty: `skillsync-prototype-seen:v2:` | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `V1_COMMENTS_PREFIX` | string |  | prefiks kluczy v1 czytanych przy imporcie: `skillsync-prototype-comments:v1:` | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `OPERATION_TYPES` | tablica |  | 15 typów operacji dziennika v2 (kolejność jak w specu W1) | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `THREAD_TYPES` | tablica |  | typy operacji wątku (11) | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `PROTOTYPE_TYPES` | tablica |  | typy operacji prototypu: `accept`, `unaccept`, `iteration`, `restore` | `EngineV2AcceptancePureTests.test_accept_types_never_queueable` |
| `QUEUEABLE_TYPES` | tablica |  | typy dopuszczone do kolejki offline (8, W1-K16) | `EngineV2AcceptancePureTests.test_accept_types_never_queueable` |
| `STATUSES` | tablica |  | statusy wątku: open, awaiting, decided, closed, deferred | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `OPEN_STATUSES` | tablica |  | statusy liczone jako otwarte: open, awaiting, decided | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `FILTERS` | tablica |  | filtry panelu: open, new, agent, lost, all (W1-K19) | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `MAX_TEXT` | number |  | maksymalna długość tekstu wpisu: 4000 jednostek UTF-16 | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `isOperation` | funkcja | 1 | (x) → czy wartość ma kształt operacji dziennika | `EngineV2ExportContractTests.test_is_operation_shape` |
| `normalizeOperations` | funkcja | 1 | (ops) → operacje po normalizacji (kroki 1–2 foldu z `comments-format.md`) | `EngineV2ExportContractTests.test_normalize_operations_drops_invalid_and_duplicate_ids_first_wins` |
| `sortOperations` | funkcja | 1 | (ops) → operacje posortowane po (`at`, `id`) w jednostkach UTF-16 | `EngineV2ExportContractTests.test_sort_operations_by_at_then_id_in_utf16_units`, `EngineV2ExportContractTests.test_merge_operations_is_union_by_id_with_first_list_winning` |
| `mergeOperations` | funkcja | 2 | (a, b) → suma dzienników po `id` (przy kolizji wygrywa wersja z a, niepoprawne operacje odpadają), posortowana po (`at`, `id`) jak `sortOperations` | `EngineV2ExportContractTests.test_merge_operations_is_union_by_id_with_first_list_winning` |
| `foldDocument` | funkcja | 1 | (doc) → stan: wątki, numery, statusy, głosy, decyzje, iteracja bieżąca, akceptacja (identyczny z foldem Pythona, W1-K5) | `EngineV2DifferentialTests.test_fold_serialization_and_people_match_node`, `EngineV2DifferentialTests.test_known_scenario_matches_node_and_contract` |
| `foldOperations` | funkcja | 1 | (ops) → jak `foldDocument` dla samej listy operacji | `EngineV2ScreenSwitcherTests.test_thread_counts_on_entries` |
| `isOpenStatus` | funkcja | 1 | (status) → czy status liczy się jako otwarty | `EngineV2ExportContractTests.test_is_open_status` |
| `openCountByScreen` | funkcja | 1 | (stan) → liczba otwartych wątków na ekran | `EngineV2ScreenSwitcherTests.test_thread_counts_on_entries` |
| `voteSummary` | funkcja | 1 | (wpis) → liczby 👍/👎 i głosujący | `EngineV2ExportContractTests.test_vote_summary_lists_voters_sorted_and_ignores_other_values` |
| `anchorKind` | funkcja | 1 | (wątek albo kotwica) → `element`, `screen` albo `lost` | `EngineV2ExportContractTests.test_anchor_kind` |
| `personFromLogin` | funkcja | 2 | (login, nazwa?) → osoba: imię, pełne imię i nazwisko (W1-K41) | `EngineV2ExportContractTests.test_person_from_login_and_name_override` |
| `computeLabels` | funkcja | 2 | (loginy, nazwy) → etykiety osób z rozwiązaniem kolizji imion i inicjałów (identyczne z Pythonem) | `EngineV2LabelTests.test_labels_python_equal_js_including_full_entries`, `EngineV2LabelTests.test_js_label_matches_spec_examples` |
| `peopleFromOperations` | funkcja | 2 | (ops, nazwy) → osoby występujące w dzienniku z etykietami | `EngineV2DifferentialTests.test_fold_serialization_and_people_match_node`, `EngineV2LabelTests.test_people_from_log_match_js_for_collisions` |
| `initialsFor` | funkcja | 1 | (osoba) → inicjały awatara | `EngineV2ExportContractTests.test_initials_for_person` |
| `avatarTone` | funkcja | 1 | (login) → deterministyczny odcień awatara | `EngineV2ExportContractTests.test_avatar_tone_is_deterministic_in_range_and_varies_by_login` |
| `threadMatches` | funkcja | 3 | (wątek, filtr, kontekst) → czy wątek należy do filtra | `EngineV2AcceptancePureTests.test_new_and_lost_filters` |
| `filterThreads` | funkcja | 3 | (wątki, filtr, kontekst) → wątki filtra pogrupowane po ekranach | `EngineV2AcceptancePureTests.test_new_and_lost_filters` |
| `filterCounts` | funkcja | 2 | (wątki, kontekst) → licznik dla każdego filtra | `EngineV2AcceptancePureTests.test_new_and_lost_filters` |
| `classifyWriteResult` | funkcja | 2 | (status HTTP, kod błędu) → klasa wyniku zapisu z kolejki (sukces, ponów, odrzuć, zatrzymaj, sesja) | `EngineV2QueueTests.test_classify_write_result` |
| `mergeQueueItems` | funkcja | 3 | (zapisane, lokalne, usunięte cid?) → kolejka po scaleniu po `cid` (kilka kart) | `EngineV2QueueTests.test_two_tabs_both_entries_survive`, `EngineV2QueueTests.test_merge_order_dedup_and_removed`, `EngineV2QueueTests.test_merge_empty_and_corrupt_inputs` |
| `pollDelay` | funkcja | 2 | (widoczna, liczba błędów z rzędu) → odstęp odpytywania (5 s widoczna, 30 s ukryta, W1-K15) | `EngineV2AcceptancePureTests.test_poll_delay_backs_off_to_five_minutes` |
| `newClientId` | funkcja | 3 | (prefiks, czas w ms?, funkcja losująca?) → nowy unikalny `cid` | `EngineV2QueueTests.test_control_random_ids_would_duplicate` |
| `canonicalOperation` | funkcja | 1 | (op) → kanoniczna postać operacji (porównania bajt w bajt) | `EngineV2ExportContractTests.test_canonical_operation_key_order_and_content` |
| `serializeCommentsDocument` | funkcja | 2 | (prototypeId, operacje) → tekst `comments.json` (jedna operacja na linię, jak serwer) | `EngineV2DifferentialTests.test_fold_serialization_and_people_match_node` |
| `normalizeCommentsDocument` | funkcja | 2 | (doc, slug) → dokument po walidacji nagłówka i operacji (tryb tylko do odczytu) | `EngineV2AcceptancePureTests.test_normalize_comments_document_ignores_foreign_documents_whole` |
| `convertV1Operations` | funkcja | 2 | (operacje v1, kontekst: znane id, znane wątki, nowe id, id importu?) → operacje v2 z importu (W1-K31) | `EngineV2QueueTests.test_convert_maps_threads_order_and_author`, `EngineV2QueueTests.test_convert_skips_known_ids_deleted_and_unknown_threads`, `EngineV2QueueTests.test_convert_real_v1_export_counts` |
| `v1ImportId` | funkcja | 2 | (rodzaj `th`, `m` albo `c`, id v1) → deterministyczny `cid` importu | `EngineV2QueueTests.test_v1_import_id_deterministic_distinct_and_server_patterns` |
| `THEME_LOCK_METAS` | tablica |  | meta blokady motywu: `color-scheme: light dark` i `darkreader-lock` | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `ensureThemeLockMetas` | funkcja | 1 | (dokument) → dopisuje brakujące meta blokady | `EngineV2ThemeLockTests.test_missing_metas_are_added_at_head_start`, `EngineV2ThemeLockTests.test_existing_metas_are_not_duplicated` |
| `isDarkReaderLeftover` | funkcja | 1 | (węzeł) → czy to pozostawiony styl rozszerzenia przyciemniającego | `EngineV2DarkReaderLeftoverTests.test_only_style_with_darkreader_class_matches` |
| `hasDarkReaderLock` | funkcja | 1 | (dokument) → czy jest meta blokady | `EngineV2ExportContractTests.test_has_dark_reader_lock` |
| `removeDarkReaderLeftovers` | funkcja | 2 | (dokument, rodzice) → usuwa pozostałości tylko przy blokadzie | `EngineV2DarkReaderLeftoverTests.test_leftover_removed_at_start`, `EngineV2DarkReaderLeftoverTests.test_without_lock_nothing_is_removed` |
| `screenEntriesFromLinks` | funkcja | 1 | (linki) → lista ekranów przełącznika (numer, nazwa, id) | `EngineV2ScreenSwitcherTests.test_entries_built_from_links` |
| `normalizeSearch` | funkcja | 1 | (tekst) → postać do wyszukiwania (bez wielkości liter i znaków diakrytycznych) | `EngineV2ExportContractTests.test_normalize_search_ignores_case_diacritics_and_extra_spaces` |
| `filterScreens` | funkcja | 2 | (ekrany, zapytanie) → ekrany pasujące po numerze albo nazwie | `EngineV2ScreenSwitcherTests.test_filter_ignores_case_and_polish_diacritics`, `EngineV2ScreenSwitcherTests.test_filter_number_is_prefix` |
| `adjacentScreen` | funkcja | 3 | (ekrany, id bieżącego, kierunek -1 albo 1) → poprzedni/następny ekran albo brak na krańcu | `EngineV2ScreenSwitcherTests.test_prev_next_at_edges` |
| `visibleScreenId` | funkcja | 2 | (pozycje ekranów z `bottom`, próg przewinięcia) → ekran uznany za bieżący | `EngineV2ScreenSwitcherTests.test_visible_screen_follows_scroll` |
| `screenListMove` | funkcja | 4 | (indeks, klawisz, liczba pozycji, kolumny) → nowy indeks w liście ekranów (strzałki, Home, End) | `EngineV2ScreenSwitcherTests.test_list_keyboard_moves`, `EngineV2ReviewR1Tests.test_search_field_keys` |
| `screenShortcut` | funkcja | 1 | (zdarzenie) → skrót przełącznika (G, ←, →) albo brak | `EngineV2ScreenSwitcherTests.test_shortcuts_do_not_fire_while_typing` |
| `screenSearchKeyAction` | funkcja | 1 | (zdarzenie) → akcja klawisza w wyszukiwaniu ekranów | `EngineV2ReviewR1Tests.test_search_field_keys`, `EngineV2AcceptancePureTests.test_search_enter_during_ime_composition_is_ignored` |
| `focusableAfter` | funkcja | 3 | (dokument, element, wykluczone?) → następny element fokusu po przełączniku | `EngineV2ReviewR2Tests.test_tab_from_search_stays_in_switcher` |
| `screenSearchTabTarget` | funkcja | 4 | (dokument, przycisk główny, lista, akcja `tab-forward` albo `tab-back`) → cel Tab z wyszukiwania ekranów | `EngineV2ReviewR2Tests.test_tab_from_search_stays_in_switcher` |
| `shouldProbeIteration` | funkcja | 3 | (pozycja iteracji, wynik sprawdzenia, czy trwa) → czy sprawdzić katalogi iteracji ponownie | `EngineV2ReviewR2Tests.test_probe_retried_after_network_error` |
| `screenEntriesWithCounts` | funkcja | 2 | (ekrany, liczniki) → ekrany z liczbą otwartych wątków | `EngineV2ScreenSwitcherTests.test_thread_counts_on_entries` |
| `toolbarFitsOneRow` | funkcja | 1 | (pomiary) → czy pasek mieści się w jednym wierszu (D23) | `EngineV2ScreenSwitcherTests.test_toolbar_one_row_only_when_it_fits` |
| `RESTORE_ROLES` | tablica |  | role uprawnione do przywracania: acceptor, developer | `EngineV2ExportContractTests.test_constant_lists_hold_the_contract_values` |
| `MAX_RESTORE_REASON` | number |  | maksymalna długość powodu przywrócenia: 500 | `EngineV2ExportContractTests.test_constants_hold_the_contract_values` |
| `iterationPath` | funkcja | 1 | (numer) → ścieżka `iterations/N/` | `EngineV2ExportContractTests.test_iteration_path_and_unchanged_text` |
| `iterationList` | funkcja | 2 | (katalogi z API albo null, operacje) → iteracje do porównania (z istnieniem katalogu) | `EngineV2ReviewR1Tests.test_iteration_list_from_log_and_api` |
| `iterationForThread` | funkcja | 4 | (iteracje, wczytane uchwyty według numeru, wątek, pomijana iteracja?) → najnowsza iteracja zawierająca `protoId` wątku (W1-K23) | `EngineV2ReviewR1Tests.test_iteration_for_thread` |
| `embedModeFrom` | funkcja | 1 | (adres) → tryb osadzenia (`compare` albo brak) | `EngineV2ReviewR1Tests.test_embed_mode_and_thread_hash` |
| `threadIdFromHash` | funkcja | 1 | (hash adresu) → `threadId` z `#thread=…` | `EngineV2ReviewR1Tests.test_embed_mode_and_thread_hash` |
| `restoreAvailability` | funkcja | 1 | (stan) → czy „Przywróć…” dostępne i powód blokady | `EngineV2ReviewR1Tests.test_restore_availability_matrix` |
| `restoreSubmitState` | funkcja | 1 | (stan) → stan przycisku zapisu w oknie przywracania | `EngineV2RestoreUnchangedTests.test_submit_state_disabled_when_unchanged_reason_irrelevant`, `EngineV2ReviewR2Tests.test_preview_failed_has_own_reason` |
| `restoreUnchangedText` | funkcja | 1 | (numer iteracji) → komunikat o ekranie identycznym z bieżącym (D27) | `EngineV2ExportContractTests.test_iteration_path_and_unchanged_text` |
| `classifyRestoreResult` | funkcja | 3 | (status, kod, dane) → klasa wyniku przywrócenia | `EngineV2RestoreUnchangedTests.test_post_200_unchanged_is_not_success`, `EngineV2ReviewR1Tests.test_classify_restore_result_cases` |
| `restoreEffects` | funkcja | 2 | (podgląd, numer iteracji) → opis skutków przywrócenia | `EngineV2ReviewR1Tests.test_restore_effects` |
| `ACCEPT_ROLES` | tablica |  | role uprawnione do akceptacji: acceptor, developer | `EngineV2AcceptancePureTests.test_accept_types_never_queueable` |
| `acceptanceSummary` | funkcja | 3 | (id ekranów, akceptacja z serwera albo null, akceptacja z foldu) → liczniki chipu (k/n, nieaktualne) | `EngineV2AcceptancePureTests.test_summary_states_and_text`, `EngineV2AcceptancePureTests.test_summary_readonly_mode_has_no_staleness` |
| `acceptAvailability` | funkcja | 1 | (stan) → czy „Akceptuj…” dostępne i powód blokady | `EngineV2AcceptancePureTests.test_availability_roles_and_blocks`, `EngineV2AcceptancePureTests.test_loading_gives_its_own_reason_instead_of_reload`, `EngineV2AcceptancePureTests.test_load_reason_names_the_real_problem_instead_of_connecting_forever` |
| `acceptSubmitState` | funkcja | 1 | (stan) → stan przycisku „Akceptuję” | `EngineV2AcceptancePureTests.test_submit_state_blocks_and_ok`, `EngineV2AcceptancePureTests.test_loading_gives_its_own_reason_instead_of_reload`, `EngineV2AcceptancePureTests.test_load_reason_names_the_real_problem_instead_of_connecting_forever` |
| `canUnaccept` | funkcja | 2 | (użytkownik, akceptacja) → czy wolno cofnąć | `EngineV2AcceptancePureTests.test_can_unaccept_own_or_developer_only_when_current`, `EngineV2AcceptancePureTests.test_pure_assertions_can_fail` |
| `acceptOffline` | funkcja | 3 | (flaga, onLine, serverOk) → czy akceptacja zablokowana jako offline | `EngineV2AcceptancePureTests.test_offline_is_the_engine_flag_or_navigator_online_false`, `EngineV2AcceptancePureTests.test_online_false_is_only_a_hint_once_the_server_answered` |
| `acceptTargetScreen` | funkcja | 2 | (bieżący, ekrany) → ekran okna otwartego z chipu | `EngineV2AcceptancePureTests.test_chip_target_is_the_current_screen_or_the_first_acceptable_one` |
| `acceptLoadReason` | funkcja | 1 | (problem) → powód blokady przy błędzie odczytu | `EngineV2AcceptancePureTests.test_load_reason_names_the_real_problem_instead_of_connecting_forever` |
| `dialogEscapeAllowed` | funkcja | 2 | (rodzaj okna, mustChange) → czy Esc zamyka okno | `EngineV2AcceptancePureTests.test_escape_is_refused_only_for_the_forced_password_window` |
| `queueItemAccepted` | funkcja | 1 | (wpis) → czy wpis kolejki ma dozwolony typ i kształt | `EngineV2AcceptancePureTests.test_queue_accepts_exactly_the_eight_k16_types` |
| `queueItemsAccepted` | funkcja | 1 | (wpisy) → wpisy dozwolone (filtr kolejki) | `EngineV2AcceptancePureTests.test_queue_items_filter_keeps_order_and_drops_everything_else` |
| `panelEscapeAction` | funkcja | 1 | (stan) → {action, confirm} dla Esc w panelu (szkic) | `EngineV2AcceptancePureTests.test_escape_in_the_panel_returns_an_action_and_whether_to_ask_about_the_draft` |
| `dialogTabTarget` | funkcja | 3 | (elementy, bieżący, shift) → cel Tab z zawinięciem w oknie | `EngineV2AcceptancePureTests.test_tab_wraps_inside_the_window` |
| `acceptDoneMessage` | funkcja | 3 | (rodzaj, treść odpowiedzi, tytuł ekranu) → komunikat po akceptacji | `EngineV2AcceptancePureTests.test_done_message_names_the_screen_the_undo_or_the_project_counts` |
| `acceptClosedOutcome` | funkcja | 6 | (wynik, rodzaj, treść odpowiedzi, tytuł, bieżący slug, powód?) → reakcja na wynik po zamknięciu okna | `EngineV2AcceptancePureTests.test_closed_window_outcome_polls_on_success_and_marks_stale_only_for_this_page`, `EngineV2AcceptancePureTests.test_outcome_faulty_results_are_detected`, `EngineV2AcceptancePureTests.test_closed_window_outcome_new_fields_and_reasons` |
| `projectSummary` | funkcja | 3 | (odpowiedź `GET /project`, bieżący slug, wersja bieżącego) → podsumowanie prototypów w oknie zakresu projektu | `EngineV2AcceptancePureTests.test_project_summary` |
| `acceptScreenBody` | funkcja | 4 | (cid, ekran, hash, wersja) → treść POST akceptacji ekranu | `EngineV2AcceptancePureTests.test_bodies_and_result_classification` |
| `unacceptBody` | funkcja | 2 | (cid, ekran) → treść POST cofnięcia | `EngineV2AcceptancePureTests.test_bodies_and_result_classification` |
| `classifyAcceptResult` | funkcja | 3 | (status, kod, dane) → klasa wyniku akceptacji (done, partial, stale, auth, error…) | `EngineV2AcceptancePureTests.test_bodies_and_result_classification`, `EngineV2AcceptancePureTests.test_interrupted_project_accept_is_partial_only_when_something_was_saved` |
| `slugList` | funkcja | 1 | (lista) → slugi do komunikatu | `EngineV2AcceptancePureTests.test_slug_list_and_accepted_line` |
| `acceptedLine` | funkcja | 3 | (wpis akceptacji, nazwa osoby albo null, czas?) → wiersz „Zaakceptowano: imię, data, iteracja (UI/CLI)” | `EngineV2AcceptancePureTests.test_slug_list_and_accepted_line` |
| `threadViewOf` | funkcja | 1 | (payload `create`) → `desktop` albo `mobile`; `mobile` tylko dla dokładnie `payload.view === "mobile"`, inaczej `desktop` (reguła foldu, `comments-format.md`) | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract`, `EngineV2ViewModeTests.test_view_of_threads_matches_between_node_and_python` |
| `createViewFields` | funkcja | 1 | (widok) → pola payloadu `create`: `{view: "mobile"}` dla mobile, `{}` dla desktop i wartości innych | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract` |
| `serverSupportsViews` | funkcja | 1 | (server z sesji) → czy `server.features` jest listą zawierającą `views` | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract` |
| `createViewDecision` | funkcja | 2 | (widok, server) → `{allowed, reason}`; mobile bez flagi `views` = `allowed: false` z komunikatem po polsku, desktop zawsze dozwolony | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract` |
| `normalizeViewFilter` | funkcja | 1 | (filtr) → `all`, `desktop` albo `mobile`; nieznana wartość daje `all` | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract` |
| `threadMatchesView` | funkcja | 2 | (wątek, filtr widoku) → czy wątek należy do filtra; `all` przepuszcza wszystko, brak `view` = desktop | `EngineV2ExportContractTests.test_view_helpers_hold_the_contract` |
| `effectiveView` | funkcja | 2 | (widok lokalny ekranu, czy ekran ma ramkę) → widok efektywny; ekran bez ramki zawsze `desktop`, brak albo niepoprawny widok lokalny daje `desktop` | `EngineV2ExportContractTests.test_effective_view_and_frame_width` |
| `viewFrameWidth` | funkcja | 1 | (dostępna szerokość) → szerokość ramki widoku mobile: min(375, dostępna), także poniżej 375 | `EngineV2ExportContractTests.test_effective_view_and_frame_width` |
| `parseViewMessage` | funkcja | 2 | (dane, kierunek `toPage`/`toFrame`) → `{type, ...pola}` bez prefiksu `skillsync-proto:` albo `null`; 6 typów do strony i 3 do ramki (D2a), inne typy, zły prefiks i niepoprawne pola odrzucone | `EngineV2ExportContractTests.test_parse_view_message_allows_only_d2a_types_with_prefix` |
| `viewMessageOriginAllowed` | funkcja | 3 | (protokół, pochodzenie nadawcy, własne pochodzenie) → czy wiadomość wolno przyjąć; http(s) wymaga równego pochodzenia, `file:` nie sprawdza pochodzenia (nieprzejrzyste; źródło ustala `event.source`) | `EngineV2ExportContractTests.test_view_message_origin_and_target` |
| `viewTargetOrigin` | funkcja | 2 | (protokół, własne pochodzenie) → docelowe pochodzenie `postMessage`: `*` dla `file:`, inaczej własne pochodzenie | `EngineV2ExportContractTests.test_view_message_origin_and_target` |
| `viewFrameThreads` | funkcja | 2 | (wątki, ekran) → wątki widoku mobile tego ekranu z kotwicą, bez treści wiadomości, etykiet i podpowiedzi (D2a) | `EngineV2ExportContractTests.test_view_frame_threads_are_mobile_of_the_screen_without_message_text` |

## Asercje właściwości zamiast wartości (kontrakt słowami nie rozstrzyga konkretu)

- `avatarTone`: deterministyczność (ten login, ten wynik), nieujemna liczba całkowita o małym zakresie, różne loginy dają więcej niż jeden odcień; dokładnego numeru tonu nie asercjujemy.
- `initialsFor`: dla imienia i nazwiska dokładnie wielkie inicjały (`AL`); dla samego imienia asercja kształtu (wielkie litery, 1-2 znaki); dla pustego wejścia niepusty napis awatara.
- `mergeOperations`: suma po `id`, kolizja = wersja z `a`, wynik posortowany po (`at`, `id`). Spec `merge-comments` dotyczy scalania pliku w gicie, nie tego eksportu.
- `voteSummary`: „liczby” to długości list zwracanych loginów (`up`, `down`, posortowane).

