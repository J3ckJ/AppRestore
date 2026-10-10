# Локальные патчи ipatool для AppRestore

База: majd/ipatool `cde7d00355e152714377b953ec57438626d3cb5a` (наш 2.6.0+redirect). Применять по порядку `git apply`. Оба патча применяются и по отдельности, файлы у них не пересекаются: это проверено на чистом `cde7d00`. Всё локально, upstream ничего не отправлялось.

| Файл | Что делает | sha256 |
|---|---|---|
| `0001-ipatool-auth-info-country.patch` | `auth info --format json` отдаёт `storeFront` (сырой `X-Set-Apple-Store-Front`, например `"143441-1,34"`) и `countryCode` (ISO) | `05d87977a554102c9b036306ec2c125febaa62433d2a080d588527a8225b7fb8` |
| `0002-ipatool-list-purchases-all.patch` | `list-purchases --all`: вся история одним вызовом, без флага поведение прежнее | `025d9919871dba636a80559614a3ca402c37cf2be6965fb7a3203f4088e54445` |

Содержимое `0001` совпадает с `maks-share/ipatool-auth-info-country.patch`. Отличается только заголовок `[PATCH 1/2]`, поэтому sha256 у файлов разные. Брать файл отсюда.

## Подключение в `packaging/build-ipatool.sh` (для Димы)
После `git checkout --detach FETCH_HEAD` и проверки хэша коммита:
```bash
patches=(
  "0001-ipatool-auth-info-country.patch 05d87977a554102c9b036306ec2c125febaa62433d2a080d588527a8225b7fb8"
  "0002-ipatool-list-purchases-all.patch 025d9919871dba636a80559614a3ca402c37cf2be6965fb7a3203f4088e54445"
)
for entry in "${patches[@]}"; do
  read -r file sum <<<"$entry"
  echo "$sum  $root/packaging/patches/$file" | shasum -a 256 -c -
  git -C "$work/src" apply --whitespace=nowarn "$root/packaging/patches/$file"
done
```
Маркеры в готовом бинарнике добавить в существующий цикл `for marker in`:
- `0002`: `"--all cannot be combined with --page or --max-results"` (в непатченном бинарнике строки нет, проверено `grep`).
- `0001`: надёжной уникальной строки нет: `countryCode` и `storeFront` встречаются и в непатченном бинарнике. Защищают проверка sha256 плюс `git apply` (оба валят сборку при несовпадении). Если нужен маркер именно в бинарнике, можно добавить в патч уникальную строку, скажите.

После патчей архивы получатся другие, поэтому нужно обновить SHA-256 в `fetch_ipatool.py` и установщиках. Имя тега или версии (`ipatool-2.6.0-redirect`) стоит сменить, чтобы не путать со старыми архивами. Это решает Евгений.

## Синтаксис `--all`
```
ipatool list-purchases --all [--platform iphone|ipad|appletv|visionos|macos] --format json --non-interactive --keychain-passphrase "$IPATOOL_KEYCHAIN_PASSPHRASE"
→ {"level":"info","count":N,"totalCount":N,"page":1,"apps":[…],"time":"…"}
```
- `--all` нельзя совмещать с `--page`/`--max-results`. Иначе ipatool вернёт `{"success":false,"error":"--all cannot be combined with --page or --max-results"}` с кодом 1, ещё до обращения к аккаунту.
- Непатченный ipatool на `--all` отвечает текстом `unknown flag: --all` (не JSON, потому что `--format` ещё не разобран) с кодом 1. `ipatool_api.py` распознаёт это как `FLAG_UNSUPPORTED` и сам переходит на страницы по 100.
