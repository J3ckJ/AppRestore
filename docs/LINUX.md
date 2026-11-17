# AppRestore 0.4.1 на Linux

GUI и CLI собираются для **x86_64**, для дистрибутивов с **glibc 2.35+**:
Ubuntu 22.04+, Debian 12+, современные Fedora и производные.
Встроенный Python не зависит от версии Python в дистрибутиве (в том числе
от нового системного Python Fedora). Alpine/musl и Linux ARM готовыми
архивами пока не поддерживаются. Эти архивы появляются после сборки изменений
из этой ветки; существующий релиз v0.4.1 автоматически не изменяется.

## USB и системные библиотеки

Fedora (включая запуск окна через XWayland в сеансе Wayland):

```sh
sudo dnf install usbmuxd libimobiledevice-utils libstdc++ libgcc mesa-libGL mesa-libEGL libglvnd-opengl fontconfig freetype \
  libxkbcommon-x11 dbus-libs glib2 xcb-util-cursor xcb-util-wm xcb-util-image \
  xcb-util-keysyms xcb-util-renderutil unzip
```

Ubuntu / Debian:

```sh
sudo apt install usbmuxd libimobiledevice-utils libstdc++6 libgcc-s1 libgl1 libegl1 libopengl0 libfontconfig1 libfreetype6 \
  libxkbcommon-x11-0 libdbus-1-3 libglib2.0-0 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 unzip
```

Для CLI достаточно usbmuxd, libimobiledevice-utils и unzip. На других
дистрибутивах установите аналогичные пакеты из их репозитория.
На Fedora Silverblue/Kinoite системные USB-пакеты ставятся на хост через
`rpm-ostree install` с последующей перезагрузкой; toolbox не заменяет
usbmuxd на хосте.

Подключите разблокированный iPhone, ответьте «Доверять» на телефоне.
`usbmuxd` обычно запускается автоматически через udev/systemd при подключении.
Если устройство не видно, проверьте `systemctl status usbmuxd`, затем
`sudo systemctl start usbmuxd` и `idevicepair pair` от обычного пользователя.
AppRestore запускайте от обычного пользователя, без sudo. Не отключайте SELinux
и не выдавайте всем пользователям права на USB-устройство: используйте правила
udev из пакета usbmuxd.

## Запуск готовой сборки

Скачайте артефакт `apprestore-linux-x86_64` из GitHub Actions →
**Linux build and distro checks** (или архивы из нового релиза после публикации).
Распакуйте артефакт, затем проверьте контрольные суммы и распакуйте нужную версию:

```sh
sha256sum -c AppRestore-GUI-Linux-x86_64.zip.sha256
unzip AppRestore-GUI-Linux-x86_64.zip
./AppRestore/AppRestore
```

```sh
sha256sum -c AppRestore-CLI-Linux-x86_64.zip.sha256
unzip AppRestore-CLI-Linux-x86_64.zip
./AppRestore-CLI/apprestore doctor
./AppRestore-CLI/apprestore devices
./AppRestore-CLI/apprestore
```

Сохраните всю папку: `_internal` и `ipatool` нужны рядом с программой.
В релизах общие контрольные суммы находятся в `SHA256SUMS.txt`.
Для проверки GUI без телефона: `./AppRestore/AppRestore --self-test`.
Если Qt не выбирает рабочий backend в Wayland-сеансе, попробуйте
`QT_QPA_PLATFORM=xcb ./AppRestore/AppRestore` (нужен XWayland).

Первый вход в Apple ID требует интернета: ipatool загружает свой SAP/Unicorn
runtime и ассеты Apple. Проверка без учётной записи не подтверждает успешный
вход, скачивание или установку на настоящий телефон.

## Данные

- кэш: `$XDG_CACHE_HOME/AppRestore`, по умолчанию `~/.cache/AppRestore`;
- постоянные данные и IPA: `$XDG_DATA_HOME/AppRestore`, по умолчанию
  `~/.local/share/AppRestore` (IPA в `ipas/`);
- зашифрованные сессии ipatool и сохранённые аккаунты: `~/.ipatool` и
  `~/.apprestore`, как в ipatool/других версиях AppRestore.

Остаются доступны `APPRESTORE_CACHE_DIR`, `APPRESTORE_DATA_DIR`,
`APPRESTORE_IPA_DIR` и `APPRESTORE_EXTRA_IPA_DIRS` (разделитель `:`).
Архивы iMazing с другого компьютера можно подключить явно через настройки
или переменные окружения; Windows-каталоги iMazing на Linux не сканируются.

## Сборка и проверка

Из корня репозитория с Linux-изменениями:

```sh
docker build -f packaging/Dockerfile.linux -t apprestore-linux .
container=$(docker create apprestore-linux)
docker cp "$container:/src/linux-dist" linux-dist
docker rm "$container"
```

Сборка в Ubuntu 22.04 фиксирует нижнюю границу glibc; сборка на новой Fedora
не подходит для распространения на старые Ubuntu. Dockerfile использует
hash-locked runtime, закреплённые инструменты сборки и тот же ipatool 2.8.1
с патчами AppRestore, что Windows/macOS. PySide6 закреплён на 6.9.3.
GUI и CLI упаковываются, затем распакованные копии проходят проверки повторно,
включая запуск встроенного pymobiledevice3 и загрузку QML.

Workflow `linux-build.yml` проверяет **те же бинарные архивы** в Ubuntu 22.04,
Ubuntu 24.04, Debian 12 и текущей Fedora: offscreen и XCB через Xvfb,
с программным и стандартным рендерером. Системные C++/fontconfig/freetype
библиотеки берутся из дистрибутива, чтобы старые копии из Ubuntu не ломали
драйверы Mesa и конфигурацию шрифтов Fedora.
Эти проверки входят в gate публикации новых релизов. Проверки контейнеров
не покрывают USB-устройство, Apple ID, рабочий стол Wayland и GPU пользователя.
