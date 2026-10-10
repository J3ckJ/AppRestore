import QtQuick
import "../theme"

// Sign in to Apple ID: email + password, then the 6-digit code (2FA).
// The password goes straight to QuickSession.login and is not kept here.
Sheet {
    id: root
    objectName: "signInSheet"
    readonly property var s: ui.signIn
    title: root.s.title || ""
    panelWidth: 560
    onCancel: ui.closeSignIn()

    Column {
        id: col
        width: parent.width
        spacing: 0

        T {
            token: "sheetSub"
            color: Theme.ink2
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.s.sub || ""
        }
        Item { width: 1; height: 22 }

        Field {
            id: email
            visible: !root.s.code
            placeholder: "Почта Apple ID"
            text: root.s.email || ""
        }
        Item { width: 1; height: 10; visible: email.visible }
        Field {
            id: password
            visible: !root.s.code
            placeholder: "Пароль"
            secret: true
        }
        Field {
            id: code
            visible: !!root.s.code
            placeholder: "Код из 6 цифр"
            digits: true
        }

        Item { width: 1; height: 12 }
        InfoLine {
            maxWidth: col.width
            visible: (root.s.status || "") !== ""
            wrap: true
            glyph: "info"
            text: root.s.status || ""
        }
        Item { width: 1; height: 20 }
        Cta {
            objectName: "signInGo"
            text: root.s.code ? "Подтвердить" : (root.s.busy ? "Входим…" : "Войти")
            enabledLook: !root.s.busy
            onClicked: root.s.code ? ui.submitCode(code.text) : ui.login(email.text, password.text)
        }
    }

    component Field: Rectangle {
        id: f
        property string placeholder: ""
        property bool secret: false
        property bool digits: false
        property alias text: input.text
        width: col.width
        height: 46
        radius: 10
        color: Theme.card
        border.width: input.activeFocus ? 2 : 1
        border.color: input.activeFocus ? Theme.ink : Theme.line
        TextInput {
            id: input
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            verticalAlignment: TextInput.AlignVCenter
            font.family: Theme.fontFamily
            font.pixelSize: f.digits ? 20 : 16
            font.letterSpacing: f.digits ? 4 : 0
            color: Theme.ink
            echoMode: f.secret ? TextInput.Password : TextInput.Normal
            inputMethodHints: f.digits ? Qt.ImhDigitsOnly : Qt.ImhNone
            maximumLength: f.digits ? 6 : 256
            clip: true
            selectByMouse: true
        }
        T {
            visible: input.text === "" && !input.activeFocus
            anchors.verticalCenter: parent.verticalCenter
            x: 14
            token: "sheetSub"
            color: Theme.ink3
            text: f.placeholder
        }
    }
}
