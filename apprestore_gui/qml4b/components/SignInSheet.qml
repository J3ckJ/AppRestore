import QtQuick
import "../theme"

// Sign in to Apple ID: email + password, then the 6-digit code (2FA).
// The password goes straight to QuickSession.login and is not kept here.
Sheet {
    id: root
    objectName: "signInSheet"
    readonly property var s: ui.signIn
    title: root.s.title || ""
    panelWidth: Theme.authSheetWidth
    onCancel: ui.cancelLogin()
    Keys.onReturnPressed: go.clicked()
    Keys.onEnterPressed: go.clicked()

    Column {
        id: col
        width: parent.width
        spacing: 0

        T {
            token: "authSub"
            color: Theme.ink2
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.StyledText
            text: root.s.sub || ""
        }
        Item { width: 1; height: 22 }

        T { visible: !root.s.code; token: "fieldLabel"; color: Theme.ink2; text: "Почта Apple ID" }
        Item { width: 1; height: 7; visible: !root.s.code }
        Field {
            id: email
            objectName: "signInEmail"
            visible: !root.s.code
            readOnly: !!root.s.emailReadOnly || !!root.s.busy
            text: root.s.email || ""
        }
        Item { width: 1; height: 16; visible: !root.s.code }
        T { visible: !root.s.code; token: "fieldLabel"; color: Theme.ink2; text: "Пароль" }
        Item { width: 1; height: 7; visible: !root.s.code }
        Field {
            id: password
            objectName: "signInPassword"
            visible: !root.s.code
            secret: true
            readOnly: !!root.s.busy
            error: (root.s.error || "") !== ""
            focus: !root.s.code
        }

        // 2FA: one hidden TextInput, six drawn cells (paste, Backspace, macOS autofill work)
        Item {
            id: codeBox
            objectName: "signInCode"
            visible: !!root.s.code
            width: 6 * Theme.codeCellW + 5 * 10 + 14
            height: Theme.codeCellH
            Accessible.role: Accessible.EditableText
            Accessible.name: "Код подтверждения, 6 цифр, введено " + codeInput.text.length
            TextInput {
                id: codeInput
                objectName: "signInCodeInput"
                opacity: 0
                width: 1; height: 1
                focus: !!root.s.code
                maximumLength: 6
                inputMethodHints: Qt.ImhDigitsOnly
                validator: RegularExpressionValidator { regularExpression: /\d{0,6}/ }
                onTextEdited: if (text.length === 6) ui.submitCode(text)
                Keys.onPressed: function(e) {
                    if (e.matches(StandardKey.Paste)) {
                        codeInput.text = ui.codeDigits(ui.clipboardText ? ui.clipboardText() : "")
                        e.accepted = true
                    }
                }
            }
            MouseArea { anchors.fill: parent; onClicked: codeInput.forceActiveFocus() }
            Row {
                spacing: 10
                Repeater {
                    model: 6
                    Rectangle {
                        width: Theme.codeCellW
                        height: Theme.codeCellH
                        radius: Theme.fieldRadius
                        x: index >= 3 ? 14 : 0
                        readonly property bool current: codeInput.activeFocus && index === Math.min(codeInput.text.length, 5)
                        color: current ? Theme.card : Theme.surfaceSoft
                        border.width: current ? 2 : 0
                        border.color: Theme.focusRing
                        T {
                            anchors.centerIn: parent
                            token: "codeDigit"
                            text: codeInput.text.charAt(index)
                        }
                    }
                }
            }
        }
        Item { width: 1; height: 12; visible: !!root.s.code }
        T {
            visible: !!root.s.code
            token: "note"; color: Theme.ink3Text
            width: parent.width; wrapMode: Text.WordWrap
            text: root.s.codeHint || ""
        }

        Item { width: 1; height: 8; visible: errorLine.visible }
        InfoLine {
            id: errorLine
            objectName: "signInError"
            maxWidth: col.width
            visible: (root.s.error || "") !== ""
            wrap: true
            glyph: "info"
            color: Theme.errorText
            text: root.s.error || ""
        }
        Item { width: 1; height: 16 }
        InfoLine {
            maxWidth: col.width
            visible: !root.s.code
            wrap: true
            glyph: "lock"
            text: root.s.fine || ""
        }
        Item { width: 1; height: 20 }
        Item {
            width: parent.width
            height: go.height
            T {
                anchors.left: parent.left
                anchors.right: go.left
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                token: "status"; color: Theme.ink2
                elide: Text.ElideRight
                text: root.s.status || ""
            }
            Cta {
                id: go
                objectName: "signInGo"
                anchors.right: parent.right
                text: root.s.go || "Войти"
                enabledLook: !root.s.busy && (root.s.code ? codeInput.text.length === 6
                                                          : (email.text.indexOf("@") > 0 && password.text.length > 0))
                onClicked: if (enabledLook) { root.s.code ? ui.submitCode(codeInput.text) : ui.login(email.text, password.text) }
            }
        }
    }

    component Field: Rectangle {
        id: f
        property string placeholder: ""
        property bool secret: false
        property bool digits: false
        property bool readOnly: false
        property bool error: false
        property alias text: input.text
        width: col.width
        height: Theme.fieldHeight
        radius: Theme.fieldRadius
        color: f.readOnly || input.activeFocus || f.error ? Theme.card : Theme.surfaceSoft
        border.width: f.error || (input.activeFocus && !f.readOnly) ? 2 : (f.readOnly ? 1 : 0)
        border.color: f.error ? Theme.accent : (f.readOnly ? Theme.line : Theme.focusRing)
        TextInput {
            id: input
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            verticalAlignment: TextInput.AlignVCenter
            font.family: Theme.fontFamily
            font.pixelSize: f.digits ? 20 : 16
            font.letterSpacing: f.digits ? 4 : 0
            color: f.readOnly ? Theme.ink2 : Theme.ink
            readOnly: f.readOnly
            activeFocusOnTab: !f.readOnly
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
            color: Theme.ink3Text
            text: f.placeholder
        }
    }
}
