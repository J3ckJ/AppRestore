import QtQuick
import "../theme"

// Left column of the four onboarding steps.
Column {
    id: root
    property int step: 1
    property string noun: "iPhone"
    width: Theme.heroWidth

    T { token: "over"; text: root.step === 1 ? "Добро пожаловать" : "Шаг " + root.step + " из 4" }
    Item { width: 1; height: 22 }
    T {
        token: "h1"
        lineHeightMode: Text.FixedHeight
        lineHeight: 84 * 0.96
        height: lineCount * 84 * 0.96
        // CSS lets glyphs overflow a tight line box (negative half-leading);
        // Qt does not, so lift by that half-leading to match the concept.
        transform: Translate { y: (84 * 0.96 - 84 * 1.21) / 2 }
        text: root.step === 1 ? "Вернём то,\nчто пропало\nс " + root.noun
            : root.step === 2 ? "Подключите\n" + root.noun
            : root.step === 3 ? "Зачем\nApple ID"
            : "Смотрю,\nчего не хватает"
    }

    // step 1, 3: lead
    Item { width: 1; height: 30; visible: lead.visible }
    T {
        id: lead
        visible: root.step === 1 || root.step === 3
        token: "lead"
        color: Theme.ink2
        width: Theme.leadWidth
        wrapMode: Text.WordWrap
        textFormat: Text.StyledText
        lineHeightMode: Text.FixedHeight
        lineHeight: 22 * 1.4
        text: root.step === 1
            ? "Сгруженные приложения и те, что убрали из App Store: банки, сервисы, игры. По кабелю, на ваш телефон. Бесплатно, с открытым кодом."
            : "Удалённые из App Store приложения есть только на серверах Apple. Скачать их можно на <font color='" + Theme.ink + "'><b>ваш</b></font> Apple ID."
    }

    // step 2: numbered steps
    Item { width: 1; height: 30; visible: root.step === 2 }
    Column {
        visible: root.step === 2
        spacing: 16
        Repeater {
            model: ["Соедините телефон с компьютером кабелем", "Разблокируйте " + root.noun, "Нажмите «Доверять» и введите код телефона"]
            Row {
                spacing: 16
                Rectangle {
                    width: 28; height: 28; radius: 14; color: "transparent"
                    border.width: 1.5; border.color: Theme.ink
                    anchors.verticalCenter: parent.verticalCenter
                    T { anchors.centerIn: parent; token: "stepNum"; text: String(index + 1) }
                }
                T { token: "step"; text: modelData; anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }
    Item { width: 1; height: 28; visible: root.step === 2 }
    InfoLine { visible: root.step === 2; glyph: "usb"; text: "Ждём телефон. Дальше перейдём сами." }

    // step 3: what needs Apple ID
    Item { width: 1; height: 26; visible: root.step === 3 }
    Column {
        visible: root.step === 3
        width: 520
        Rectangle { width: parent.width; height: 1; color: Theme.ink }
        Repeater {
            model: [ { t: "Сгруженные", v: "не нужен", need: false }, { t: "Свой файл IPA", v: "не нужен", need: false }, { t: "Удалённые из App Store", v: "нужен", need: true } ]
            Item {
                width: 520; height: 12 + 22 + 12 + 1
                T { y: 12; token: "table"; text: modelData.t }
                T { y: 12; anchors.right: parent.right; token: "table"; font.weight: 650; color: modelData.need ? Theme.accent : Theme.ok; text: modelData.v }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            }
        }
    }

    // step 4: the check counter
    Item { width: 1; height: 30; visible: root.step === 4 }
    Column {
        visible: root.step === 4
        width: 520
        Rectangle {
            width: 520; height: 10; radius: 5; color: Theme.stepOkBg; clip: true
            Rectangle { height: parent.height; radius: 5; width: parent.width * ui.scanFraction; color: Theme.ink }
        }
        Item { width: 1; height: 12 }
        Item {
            width: 520; height: cap.height
            T { id: cap; token: "scanCap"; text: ui.scanCaption }
            T { anchors.right: parent.right; token: "scanCap"; font.weight: 450; color: Theme.ink2; text: ui.scanEta }
        }
        Item { width: 1; height: 22 }
        Row {
            spacing: 28
            Repeater {
                model: ui.scanFound
                Column {
                    T { token: "foundNum"; text: String(modelData.count) }
                    T { token: "found"; color: Theme.ink2; text: modelData.label }
                }
            }
        }
    }

    // buttons
    Item { width: 1; height: root.step === 3 ? 30 : 34; visible: root.step === 1 || root.step === 3 }
    Cta {
        visible: root.step === 1 || root.step === 3
        text: root.step === 1 ? "Начать" : "Войти в Apple ID"
        onClicked: root.step === 1 ? ui.onboardingStart() : signInRequested()
    }
    signal signInRequested()
    Item { width: 1; height: 16; visible: root.step === 3 }
    InfoLine {
        visible: root.step === 3
        glyph: "lock"
        wrap: true
        text: "Вход хранится только на этом компьютере, в связке ключей под вашим паролем. Код придёт на ваши устройства Apple."
    }
    Item { width: 1; height: root.step === 4 ? 26 : (root.step === 3 ? 20 : 30) }
    InfoLine { visible: root.step === 4; text: "Телефоном можно пользоваться, только не отключайте кабель." }
    Links {
        // only links that lead somewhere that exists (Евгений): «Позже» on step 3
        visible: root.step === 3
        items: ["Позже"]
        onActivated: function(name) { if (name === "Позже") ui.onboardingLater() }
    }
}
