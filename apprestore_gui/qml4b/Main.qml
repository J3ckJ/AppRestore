import QtQuick
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
        if (!win.onboarding && !ui.pickerOpen && !ui.signIn.open && !ui.consent.open && (win.home.cta || "") !== "")
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
        onLink: function(name) { if (name === "Остановить") ui.stop() }
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
        off: !win.home.pillOn && alert === ""
        pageDots: win.home.pages || 0
    }

    // -- «Что вернуть» -------------------------------------------------------------
    Loader {
        anchors.fill: parent
        active: ui.pickerOpen
        sourceComponent: PickerSheet {}
    }

    // -- sign-in with 2FA ------------------------------------------------------------
    Loader {
        anchors.fill: parent
        active: !!ui.signIn.open
        focus: active
        sourceComponent: SignInSheet {}
    }

    // -- free licenses: consent before the run -----------------------------------------
    Loader {
        anchors.fill: parent
        active: !!ui.consent.open
        focus: active
        sourceComponent: ConsentSheet {}
    }
}
