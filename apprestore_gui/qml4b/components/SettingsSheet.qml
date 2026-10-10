import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme"

// «Настройки» (02-picker §6b): «Поиск» — «Искать в архиве» (on by default,
// applied at once) with Лена's caption; «Обновления» — the old window's check.
Sheet {
    id: root
    objectName: "settingsSheet"
    readonly property var s: ui.settings
    title: root.s.title || ""
    cancelText: root.s.close || "Закрыть"
    panelWidth: Theme.authSheetWidth
    onCancel: ui.closeSettings()

    Column {
        width: parent.width
        spacing: 0
        // -- Поиск
        T { token: "group"; text: root.s.searchTitle || "" }
        Item { width: 1; height: 8 }
        Rectangle { width: parent.width; height: 1; color: Theme.ink }
        Item { width: 1; height: 12 }
        Basic.Switch {
            id: archive
            objectName: "archiveSwitch"
            checked: !!root.s.archive
            text: root.s.archiveSwitch || ""
            leftPadding: 0
            font.family: Theme.fontFamily
            font.pixelSize: Theme.style("row").size
            onToggled: ui.setArchiveSearch(checked)
            Accessible.name: text
        }
        Item { width: 1; height: 8 }
        T {
            width: parent.width
            token: "fine"
            color: Theme.ink2
            wrapMode: Text.WordWrap
            text: root.s.archiveNote || ""
        }
        Item { width: 1; height: 28 }
        // -- Обновления
        T { token: "group"; text: root.s.updatesTitle || "" }
        Item { width: 1; height: 8 }
        Rectangle { width: parent.width; height: 1; color: Theme.ink }
        Item { width: 1; height: 12 }
        Cta {
            objectName: "checkUpdates"
            secondary: true
            compact: true
            enabledLook: !root.s.updateBusy
            text: root.s.checkUpdates || ""
            onClicked: ui.checkUpdates()
        }
        Item { width: 1; height: 8; visible: updateText.visible }
        T {
            id: updateText
            objectName: "updateStatus"
            visible: text !== ""
            width: parent.width
            token: "status"
            color: Theme.ink2
            wrapMode: Text.WordWrap
            text: root.s.updateStatus || ""
        }
    }
}
