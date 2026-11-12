# Локальные патчи ipatool для AppRestore

База: majd/ipatool **v2.8.1** `3e84853e2e5d60540a5022b64895d496910144eb`.
С 6 ноября 2026 Apple принимает вход в App Store только по новому протоколу
(локальная Anisette, provisioning, Store AMD-подписи). ipatool 2.6.0 получал
пустой HTTP 204, поэтому патчи перенесены на 2.8.1.

Применять строго по порядку 0001–0008. Полную последовательность, SHA-256,
Go-тесты и маркеры бинарника проверяет `packaging/build-ipatool.sh`.
Исходники серии лежат как отдельные коммиты поверх v2.8.1; патчи — это
`git diff --full-index` каждого коммита.

| Файл | Что делает | sha256 |
|---|---|---|
| `0001-ipatool-auth-info-country.patch` | `auth info --format json` отдаёт `storeFront` и `countryCode` (ISO). В 2.8 список витрин загружается из сети, поэтому для `auth info` и диагностики добавлена офлайн-таблица из 2.6.0 | `8ed499c69be593789cc3fd05de4a220f8779be102ec5bc0a3e3b653346cfda6d` |
| `0002-ipatool-list-purchases-all.patch` | `list-purchases --all`: вся история одним вызовом, без флага поведение прежнее | `035e6760ffdec8aedfe957bc3a3ea82dc2c0a6bfc7054afc1c6fa4eefb6e8e32` |
| `0003-ipatool-keychain-passphrase-env-stdin.patch` | пароль связки из env `IPATOOL_KEYCHAIN_PASSPHRASE` или `--keychain-passphrase-stdin`; AppRestore использует только stdin | `d1eb59ff504e1986735e46e9a1b4c81901b22ee746a1eb545a952e68a1aac701` |
| `0004-ipatool-readonly-download-probe.patch` | `probe-download`: сравнение каналов по метаданным без покупки, загрузки IPA и приватных полей (бывший 0005) | `cbe7fe7e62d417f8e729498adaf81196a5996eba0bee0259e329cf9647200837` |
| `0005-ipatool-purchase-locker.patch` | `list-purchases --all` дополняет историю собственным locker этого Apple ID на его storefront (бывший 0009) | `f4d63cd207469c19cc06d2a0d48b67ace0df665ae3a9a3c73c0036200b9637d5` |
| `0006-ipatool-product-post-redirects.patch` | POST продукта сохраняется при редиректах Apple (ограничены хост, путь, параметры, число переходов); Store AMD-подпись 2.8 пересчитывается для каждого адреса; `probe-download` идёт тем же подписанным путём | `1d9c10872990e9fff6802de101726ddbad13f26fedd2054ba202dae8f6691251` |
| `0007-ipatool-purchase-evidence.patch` | HTTP 500 не считается лицензией; безопасные результаты каждого STDQ/GAME запроса (`purchaseAttempt`) | `e4b25df28cc2e8c7e01aae855d556c8d49e8437ffb0590e56ed515d64088632e` |
| `0008-ipatool-unavailable-redownload-update-probe.patch` | пустой ответ volumeStore 2059 тоже ведёт в read-only канал redownload; `probe-download` смотрит канал `update_unpinned` (остаток бывших 0004/0008) | `fcfb39ecab336f7a865c0b7beea1b859ded469e333acba9a31c0f5a96684850d` |

Убраны при переходе на 2.8.1 (покрыто upstream):

- бывший `0004-ipatool-delisted-redownload` — 2.8.0/2.8.1 сами повторно
  скачивают снятые с App Store приложения (unpinned redownload + ent/download,
  проверка ID и bundle id, issues majd/ipatool#654, #656, #662);
- бывший `0008-ipatool-unpinned-update` как запасной путь скачивания —
  upstream сознательно ограничивает восстановление снятых приложений каналами
  redownload и ent/download (тесты «does not … try updateProduct»). Осталась
  только диагностика этого канала в `probe-download`.

Патчи 0006–0008 не добавляют повторных покупок и не меняют цену или storefront.
Неопределённая покупка возвращает `purchase response unconfirmed`; AppRestore
учитывает её как `purchase_uncertain`. В JSON выводятся только разрешённые
поля `purchaseAttempt`, а не сырой ответ Apple.

Содержимое `0001` совпадает с `maks-share/ipatool-auth-info-country.patch`. Отличается только заголовок `[PATCH 1/2]`, поэтому sha256 у файлов разные. Брать файл отсюда.

## Синтаксис `--all`
```
ipatool list-purchases --all [--platform iphone|ipad|appletv|visionos|macos] --format json --non-interactive --keychain-passphrase "$IPATOOL_KEYCHAIN_PASSPHRASE"
→ {"level":"info","count":N,"totalCount":N,"page":1,"apps":[…],"time":"…"}
```
- `--all` нельзя совмещать с `--page`/`--max-results`. Иначе ipatool вернёт `{"success":false,"error":"--all cannot be combined with --page or --max-results"}` с кодом 1, ещё до обращения к аккаунту.
- Непатченный ipatool на `--all` отвечает текстом `unknown flag: --all` (не JSON, потому что `--format` ещё не разобран) с кодом 1. `ipatool_api.py` распознаёт это как `FLAG_UNSUPPORTED` и сам переходит на страницы по 100.

## Пароль связки без argv (0003)
```
IPATOOL_KEYCHAIN_PASSPHRASE=… ipatool <команда> --format json --non-interactive          # env
printf '%s\n' "$PASS" | ipatool <команда> --keychain-passphrase-stdin --non-interactive  # stdin
```
- stdin читается только с явным `--keychain-passphrase-stdin`, один раз и только когда связке нужен пароль. Читается побайтно до `\n`, остальной ввод не трогается. Если флаг задан, `IPATOOL_KEYCHAIN_PASSPHRASE` не читается вообще (случайный env не перебивает stdin).
- Без флага и без env поведение прежнее: в интерактиве prompt, в `--non-interactive` ошибка `keychain passphrase is required …`. Текст ошибки теперь ещё называет env и `--keychain-passphrase-stdin`.
- Старый ipatool env игнорирует (ошибка «passphrase is required»), а на `--keychain-passphrase-stdin` отвечает `unknown flag`. Поддержку определяем по `ipatool --help`: есть ли там `--keychain-passphrase-stdin`.
- env дочернего процесса читает только тот же пользователь или root (`/proc/<pid>/environ`, mode 400), а argv (`/proc/<pid>/cmdline`, `ps`) виден всем.
