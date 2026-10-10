import QtQuick
import Qt.labs.platform as Platform

// macOS only (loaded from Main.qml): «Настройки…» in the application menu, ⌘,.
Platform.MenuBar {
    Platform.Menu {
        title: "AppRestore"
        Platform.MenuItem {
            text: "Настройки…"
            role: Platform.MenuItem.PreferencesRole
            shortcut: StandardKey.Preferences
            onTriggered: ui.openSettings()
        }
    }
}
