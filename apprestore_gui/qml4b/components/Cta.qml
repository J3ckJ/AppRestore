import QtQuick
import QtQuick.Templates as Tpl
import "../theme"

// PrimaryButton / SecondaryButton (спека §1.1, §2.2): h 68, padX 46,
// радиус 18 (Windows 8). Hover/pressed — 120 мс; disabled — accentDisabled,
// без руки. Кольцо фокуса только при навигации с клавиатуры (visualFocus):
// 2px ink с отступом 3 и внутренним 1px #FFF. На Windows у кнопок стрелка.
Tpl.AbstractButton {
    id: root
    property bool secondary: false
    property bool enabledLook: true
    //: sheet buttons (goHeight/goRadius, like «Вернуть N» in the picker)
    property bool compact: false
    readonly property bool live: enabled
    enabled: enabledLook
    implicitHeight: compact ? Theme.goHeight : Theme.ctaHeight
    implicitWidth: label.implicitWidth + 2 * (compact ? 24 : Theme.ctaPadX)
    height: implicitHeight
    width: implicitWidth
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    Accessible.name: text

    Keys.onReturnPressed: if (root.live) root.clicked()
    Keys.onEnterPressed: if (root.live) root.clicked()

    contentItem: T {
        id: label
        Accessible.ignored: true
        token: root.compact ? "go" : "button"
        text: root.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        color: root.secondary ? (root.live ? Theme.ink : Theme.ink3) : Theme.inkOnAccent
    }
    background: Rectangle {
        radius: root.compact ? Theme.goRadius : Theme.ctaRadius
        color: root.secondary
               ? (root.pressed && root.live ? Theme.secondaryPressed : root.hovered && root.live ? Theme.surfaceSoft : Theme.card)
               : (!root.live ? Theme.accentDisabled
                  : root.pressed ? Theme.accentPressed
                  : root.hovered ? Theme.accentHover : Theme.accent)
        Behavior on color { ColorAnimation { duration: 120; easing.type: Theme.easeOut } }
        border.width: root.secondary ? (root.compact ? 1.5 : Theme.hair) : 0
        border.color: root.compact ? Theme.ink : Theme.line
        Rectangle {
            visible: root.visualFocus
            anchors.fill: parent
            anchors.margins: -3
            radius: parent.radius + 3
            color: "transparent"
            border.width: 2
            border.color: Theme.focusRing
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: parent.radius - 2
                color: "transparent"
                border.width: 1
                border.color: Theme.focusRingInner
            }
        }
    }
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        cursorShape: root.live && !Theme.isWin ? Qt.PointingHandCursor : Qt.ArrowCursor
    }
}
