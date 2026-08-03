# История изменений

Все заметные изменения проекта документируются в этом файле.

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
