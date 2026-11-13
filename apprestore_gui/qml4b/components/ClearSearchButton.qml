import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme"

Basic.AbstractButton {
    id: control
    width: 28
    height: 28
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: "Очистить поиск"
    Accessible.role: Accessible.Button
    background: Rectangle {
        radius: Theme.radiusSearch
        color: control.down ? Theme.line : (control.hovered ? Theme.soft : "transparent")
        border.width: control.activeFocus ? 1 : 0
        border.color: Theme.focusRing
    }
    contentItem: T {
        token: "search"
        text: "×"
        color: Theme.ink2
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    Basic.ToolTip.visible: hovered
    Basic.ToolTip.delay: 700
    Basic.ToolTip.text: "Очистить поиск · Esc"
}
