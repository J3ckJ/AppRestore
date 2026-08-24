# История изменений

Все заметные изменения проекта документируются в этом файле.

## Не выпущено

Пока нет изменений.

## 0.2.1 — 2026-08-24

### Исправлено

- пиннутая версия `ipatool` обновлена с 2.3.1 до 2.3.2: апстрим 2.3.2
  добавил откат на legacy Store-авторизацию, когда основной путь входа
  недоступен, что чинит `apprestore auth` / вход `A`, падавший с
  `unexpected response from Apple (HTTP 403): empty or non-plist body`
  после изменений на стороне Apple; SHA-256 архивов для Windows amd64,
  macOS amd64 и macOS arm64 обновлены вместе с версией.

### Изменено

- интерактивное меню (пункт `1`, сгруженные) теперь само предлагает
  подтвердить, что iPhone больше не докачивает приложение, и повторяет
  restore с `--skip-device-redownload`, если штатный redownload не
  стартовал или не завершился вовремя; раньше выход был только через
  ручной перезапуск `apprestore restore --select ... --skip-device-redownload`.

## 0.2.0 — 2026-08-05

### Добавлено

- явные состояния приложения на iPhone: `offloaded`, `downloading`, `installed`,
  `absent`, `unknown`, с bounded polling вместо фиксированной паузы;
- обязательная проверка postcondition после IPA install: успех только когда
  устройство подтверждает установленный bundle ID;
- приватный staging-снимок IPA с no-follow/open identity checks и SHA-256 за
  один проход, закрывающий подмену исходного пути между verify и install;
- SQLite WAL-индекс IPA по `path + size + mtime_ns`, безопасный для нескольких
  процессов и ускоряющий повторные сканирования;
- schema v3 для `known-apps.json`: bundle-only история, provenance,
  монотонный статус, транзитивный identity merge, миграция v1/v2,
  межпроцессная блокировка и durable atomic replace;
- hash-locked Python-зависимости и воспроизводимый vendored wheel для пакета,
  который не публикует готовый wheel;
- release gate: Windows/macOS matrix, tag/version/main checks, двойная
  побайтовая сборка и публикация только проверенных assets.

### Изменено

- сетевые источники, storefront lookup и web discovery выполняются параллельно
  с общим лимитом соединений, дедлайнами, early stop и детерминированным merge;
- добавлены bounded LRU/TTL-кэши, MIME-проверки и лимиты ответа 4 MiB (JSON) /
  2 MiB (HTML); пустые ответы и ошибки живут только 5 секунд;
- поиск больше не записывает все результаты в историю: только явный выбор или
  подтверждённый download/restore;
- `ipatool --purchase` запускается только с явным `--acquire-license`;
- macOS quick start закреплён как две команды: установка и запуск;
- macOS installer больше не требует Homebrew: он проверяет и ставит отдельный
  pinned CPython 3.12, готовит runtime целиком в staging и восстанавливает
  предыдущую managed-версию после прерванного обновления;
- поддерживаемый Python ограничен проверенным диапазоном 3.10–3.13;
- machine-readable команды сохраняют stdout только для одного JSON-документа,
  включая ошибки CLI, не открывают скрытые prompts, а прогресс дочерних
  процессов направляют в stderr;
- ответы App Store принимаются только при точном совпадении store ID и bundle
  ID; некорректные идентификаторы, redirect-домены и записи кэша отбрасываются;
- повторная установка выполняется той же bootstrap-командой и явно описана в
  README вместе с безопасным применением `--skip-device-redownload`.

### Безопасность

- Windows bootstrap проверяет package identity, publisher и Authenticode
  Microsoft Desktop App Installer; PATH-resolved `winget`/`sc.exe` и UAC retry
  полностью удалены;
- Windows update сериализован per-user mutex, восстанавливает единственный
  валидный backup до Python/сети и не оставляет live-установку наполовину
  заменённой;
- повреждённая, слишком большая или более новая версия локальной истории
  больше не перезаписывается; пользовательская операция завершается с
  предупреждением, а исходный документ сохраняется;
- загрузка и установка получили phase timeouts и cleanup при отмене;
- `doctor` выявляет несовпадение runtime/metadata, editable checkout и
  неподдерживаемый Python вместо молчаливого запуска чужой версии;
- managed launchers очищают Python environment и запускают runtime с `-I`;
  дочерние команды не используют shell, ограничивают stdout/stderr и при
  timeout завершают всё дерево процессов;
- IPA commit и staging перепроверяют SHA-256 и identity на границах операции,
  включая подмены с прежними размером и timestamp;
- сторонние GitHub Actions закреплены полными commit SHA, а существующий
  GitHub Release нельзя молча перезаписать.

## 0.1.6 — 2026-08-04

### Изменено

- упрощено интерактивное меню: `1`–`4` / `A` / `B` вместо дублирующих
  списков, отдельного поиска и трёх пунктов про IPA;
- в пункте `2` имя без префикса `search` трактуется как поисковый запрос;
- укорочены промпты и пояснения в мастерах restore;
- `apprestore download` принимает App Store ID / URL как позиционный аргумент
  (как в меню локальных IPA).

## 0.1.5 — 2026-08-03

### Добавлено

- отдельный сценарий восстановления удалённых приложений без ярлыка/placeholder:
  пункт меню `2`, команды `apprestore missing` и `apprestore restore-missing`;
- кандидаты собираются из iMazing `Apps.plist`, локальной библиотеки IPA и
  `known-apps.json`, минус всё, что ещё зарегистрировано на iPhone
  (включая offloaded placeholders);
- восстановление по App Store ID / URL без заранее известного bundle ID
  (`restore-missing --store-id`, ввод `id…` / ссылки в меню `2`);
- поиск по имени: iTunes Search API + архив IPA Filezone + веб-поиск ссылок
  `apps.apple.com` (пункт меню `S`, `apprestore search`, `search <имя>` в
  мастере пункта `2`); найденные ID запоминаются в `known-apps.json`;
- алиасы/подсказки для переименованных приложений (Домклик → ДКлик,
  Сбер/СБОЛ/SBOL → исторический App Store ID Сбербанк Онлайн);
- ручной ввод bundle ID, если истории iMazing нет;
- restore удалённых без ярлыка не вызывает штатный redownload placeholder —
  только локальный IPA или загрузка через `ipatool`.

## 0.1.4 — 2026-07-29

### Добавлено

- macOS bootstrap `install.sh`: одна проверяемая команда загружает закреплённый
  source ZIP, сверяет SHA-256 и запускает user-space установку без `sudo`;
- команда `apprestore` устанавливается в `~/.local/bin`, а PATH настраивается
  для текущего терминала явным `export` и сохраняется для новых shell;
- macOS release/bootstrap contract и CI-проверки безопасной упаковки;
- Windows-обновление теперь полностью готовится и проверяется в staging,
  а неудачная подмена автоматически возвращает предыдущую рабочую версию;
- install/uninstall принимают только каталог с AppRestore marker либо строгим
  SHA-256 fingerprint опубликованной v0.1.3 и не удаляют произвольный объект.

### Безопасность

- Windows-пути установки вычисляются через Known Folder API, а launcher
  использует переносимый `python.exe -m apprestore_core.cli`;
- macOS venv получает exact managed marker; чужой venv/launcher не заменяется,
  а ошибка удаления старого backup не отменяет уже проверенную установку;
- release builder отвергает symlink/reparse/non-regular inputs, проверяет
  согласованность версий и записывает корректный regular-file mode в ZIP.

## 0.1.3 — 2026-07-28

### Добавлено

- публичный Windows online-bootstrap `install.ps1` с pinned source ZIP и SHA-256;
- интерактивное меню с ASCII-логотипом;
- установка/удаление в user-scope без обязательного повышения прав.

### Изменено

- более строгая модель установки и проверки зависимостей.

## 0.1.2 — 2026-07-27

Внутренние итерации до первой публичной упаковки.

## 0.1.1 — 2026-07-26

Первые стабильные сценарии restore для offloaded-приложений.

## 0.1.0 — 2026-07-25

Начальная версия ядра AppRestore.
