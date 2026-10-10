import QtQuick
import QtQml
import QtQuick.Dialogs
import QtQuick.Window
import "theme"
import "components"

// AppRestore, design 4b. Context properties: ui (Restore4b), iconBook.
Window {
    id: win
    objectName: "ui4bWindow"
    width: Theme.windowWidth
    height: Theme.windowHeight
    minimumWidth: Theme.windowMinWidth
    minimumHeight: Theme.windowMinHeight
    visible: true
    color: Theme.bg
    title: "AppRestore"

    readonly property var home: ui.home
    readonly property bool onboarding: ui.screen === "onboarding"
    readonly property string noun: (home.pill || "iPhone").split(" ")[0]

    // Ресайз (спека §2.1): телефон справа, низ всегда режется окном.
    readonly property real phoneW: Math.max(360, Math.min(Theme.phoneW, 0.309 * width))
    readonly property real phoneRight: Theme.phoneRightMargin * width / Theme.windowWidth
    readonly property real heroW: Math.min(Theme.heroWidth, width - Theme.padX - phoneW - phoneRight - 48)
    readonly property real heroTop: height < 820 ? 120 : Theme.heroTop

    // Enter без фокуса = главная кнопка (спека §5)
    Item {
        id: keys
        focus: true
        Keys.onReturnPressed: win.enter()
        Keys.onEnterPressed: win.enter()
    }
    function enter() {
        if (!win.onboarding && !ui.pickerOpen && !ui.signIn.open && !ui.consent.open && !ui.account.open && !ui.find.open && !ui.settings.open && !ui.files.open && (win.home.cta || "") !== "")
            ui.primaryAction()
    }

    // -- header -----------------------------------------------------------------
    Row {
        id: brand
        x: Theme.padX
        y: Theme.topY + (Theme.pillHeight - Theme.brandIcon) / 2
        spacing: 12
        Image {
            width: Theme.brandIcon; height: Theme.brandIcon
            source: Qt.resolvedUrl("../resources/icons/app-icon-256.png")
            sourceSize.width: 68; sourceSize.height: 68
            smooth: true; mipmap: true
        }
        T { token: "brand"; text: "AppRestore"; anchors.verticalCenter: parent.verticalCenter }
    }
    Stepper {
        visible: win.onboarding
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.topY
        steps: ui.stepper
    }
    Pill {
        anchors.right: parent.right
        anchors.rightMargin: Theme.padX
        y: Theme.topY
        text: win.home.pill || ""
        on: !!win.home.pillOn
    }

    // -- left column --------------------------------------------------------------
    HomeHero {
        id: hero
        objectName: "homeHero"
        visible: !win.onboarding
        x: Theme.padX
        y: win.heroTop
        width: win.heroW
        view: win.home
        onPrimary: ui.primaryAction()
        onSecondary: ui.secondaryAction()
        onLink: function(name) { ui.link(name) }
    }
    Onboarding {
        visible: win.onboarding
        x: Theme.padX
        y: win.heroTop
        step: ui.onboardingStep
        noun: win.noun
        onSignInRequested: ui.openSignIn()
    }

    // -- phone ---------------------------------------------------------------------
    PhoneMock {
        x: win.width - win.phoneRight - win.phoneW
        y: Theme.phoneTop
        w: win.phoneW
        tiles: win.home.tiles || []
        accessibleText: win.home.phoneA11y || ""
        alert: win.onboarding && ui.onboardingStep === 2 ? "trust"
               : win.onboarding && ui.onboardingStep === 3 ? "code" : ""
        // step 1: an illustration (home screen with empty places), not a dark screen
        off: !win.home.pillOn && alert === "" && !(win.onboarding && ui.onboardingStep === 1)
        pageDots: win.home.pages || 0
    }

    // -- «Что вернуть» -------------------------------------------------------------
    Loader {
        anchors.fill: parent
        active: ui.pickerOpen
        sourceComponent: PickerSheet {}
    }

    // -- «Apple ID» when signed in: who, «Выйти» (04-auth §2a) -------------------------
    Loader {
        anchors.fill: parent
        active: !!ui.account.open
        focus: active
        sourceComponent: AccountSheet {}
    }

    // -- «Файлы IPA» (as in 0.3.2): library, «Выгрузить с устройства», «Выбрать на ПК» --
    Loader {
        anchors.fill: parent
        active: !!ui.files.open
        focus: active
        sourceComponent: FilesSheet {}
    }

    // -- «Найти» (02-picker §6b) ------------------------------------------------------
    Loader {
        anchors.fill: parent
        active: ui.find.open
        focus: active && !ui.signIn.open
        sourceComponent: FindSheet {}
    }

    // -- sign-in with 2FA: above «Найти» (its «Поставить»/«Войти» opens it over the results)
    Loader {
        anchors.fill: parent
        active: !!ui.signIn.open
        focus: active
        sourceComponent: SignInSheet {}
    }

    // -- «Настройки»: macOS menu «Настройки…» + ⌘, ; Windows Ctrl+, and a quiet link --
    Loader {
        anchors.fill: parent
        active: !!ui.settings.open
        focus: active
        sourceComponent: SettingsSheet {}
    }
    Shortcut {
        // "Ctrl" is ⌘ on macOS; there the menu item carries the same shortcut
        enabled: Qt.platform.os !== "osx"
        sequence: "Ctrl+,"
        onActivated: ui.openSettings()
    }
    Instantiator {
        // a QObject (native menu bar), so not a Loader; created on macOS only
        model: Qt.platform.os === "osx" ? 1 : 0
        delegate: MacMenu {}
    }

    // -- free licenses: consent before the run -----------------------------------------
    Loader {
        anchors.fill: parent
        active: !!ui.consent.open
        focus: active
        sourceComponent: ConsentSheet {}
    }

    // «Файлы IPA» / «Есть файл IPA для …»: only a file already on this computer
    FileDialog {
        id: ipaDialog
        title: "Файл IPA на этом компьютере"
        nameFilters: ["Файлы IPA (*.ipa)"]
        onAccepted: ui.installIpaFile(selectedFile.toString())
    }
    Connections {
        target: ui
        function onPickIpaRequested() { ipaDialog.open() }
    }
}
