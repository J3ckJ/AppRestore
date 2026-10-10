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
    onCancel: { root.forgetCode(); ui.cancelLogin() }
    Keys.onReturnPressed: go.clicked()
    Keys.onEnterPressed: go.clicked()

    // A code belongs to one sign-in (Лена п.3, Ника §3): the field is emptied right
    // after «Подтвердить», on every new prompt and on any exit from the sheet; an old
    // code would be rejected (TWO_FACTOR_REJECTED). After a failed sign-in the password
    // is typed again.
    readonly property bool isWindows: Qt.platform.os === "windows"
    readonly property bool codePhase: !!root.s.code
    readonly property string errorText: root.s.error || ""
    readonly property string codeText: root.isWindows ? codeField.text : codeInput.text
    onCodePhaseChanged: root.forgetCode()
    onErrorTextChanged: if (errorText !== "") password.text = ""
    onVisibleChanged: if (!visible) { root.forgetCode(); password.text = "" }
    Component.onDestruction: root.forgetCode()

    function forgetCode() {
        codeInput.text = ""
        codeField.text = ""
    }
    // only «Подтвердить» / Enter, only with exactly six digits — no auto-submit
    function submitCode() {
        var code = root.codeText
        root.forgetCode()
        if (/^\d{6}$/.test(code)) ui.submitCode(code)
        code = ""
    }

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
            visible: !!root.s.code && !root.isWindows
            width: 6 * Theme.codeCellW + 5 * 10 + 14
            height: Theme.codeCellH
            Accessible.role: Accessible.EditableText
            Accessible.name: "Код подтверждения, 6 цифр, введено " + codeInput.text.length
            TextInput {
                id: codeInput
                objectName: "signInCodeInput"
                opacity: 0
                width: 1; height: 1
                focus: !!root.s.code && !root.isWindows
                readOnly: !!root.s.busy
                maximumLength: 6
                inputMethodHints: Qt.ImhDigitsOnly
                validator: RegularExpressionValidator { regularExpression: /\d{0,6}/ }
                Keys.onPressed: function(e) {
                    if (e.matches(StandardKey.Paste) && !codeInput.readOnly) {
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
        // Windows: one plain field «Код из 6 цифр» instead of the cells (Ника §3)
        Field {
            id: codeField
            objectName: "signInCodeField"
            visible: !!root.s.code && root.isWindows
            digits: true
            readOnly: !!root.s.busy
            placeholder: root.s.codePlaceholder || ""
            focus: !!root.s.code && root.isWindows
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
        // «Код не пришёл? На iPhone: …» — under the code field and under the
        // auth-code-wrong error; quiet, wraps to two lines, never elided
        Item { width: 1; height: 12; visible: codeHint.visible }
        T {
            id: codeHint
            objectName: "signInCodeHint"
            visible: (root.s.hint || "") !== ""
            token: "note"; color: Theme.ink2
            width: parent.width; wrapMode: Text.WordWrap
            elide: Text.ElideNone
            text: root.s.hint || ""
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
                enabledLook: !root.s.busy && (root.s.code ? /^\d{6}$/.test(root.codeText)
                                                          : (email.text.indexOf("@") > 0 && password.text.length > 0))
                onClicked: if (enabledLook) { root.s.code ? root.submitCode() : ui.login(email.text, password.text) }
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
            validator: f.digits ? digitsOnly : null
            clip: true
            selectByMouse: true
        }
        RegularExpressionValidator { id: digitsOnly; regularExpression: /\d{0,6}/ }
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
