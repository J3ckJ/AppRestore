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
    minimumWidth: 1180
    minimumHeight: 760
    visible: true
    color: Theme.bg
    title: "AppRestore"

    readonly property var home: ui.home
    readonly property bool onboarding: ui.screen === "onboarding"
    readonly property string noun: (home.pill || "iPhone").split(" ")[0]

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
        y: Theme.heroTop
        view: win.home
        onPrimary: ui.primaryAction()
        onLink: function(name) { if (name === "Остановить") ui.stop() }
    }
    Onboarding {
        visible: win.onboarding
        x: Theme.padX
        y: Theme.heroTop
        step: ui.onboardingStep
        noun: win.noun
    }

    // -- phone ---------------------------------------------------------------------
    PhoneMock {
        x: Theme.phoneX
        y: Theme.phoneY
        tiles: win.home.tiles || []
        off: !win.home.pillOn
        pageDots: win.home.state === "many" ? 11 : 0
    }

    // -- «Что вернуть» -------------------------------------------------------------
    Loader {
        anchors.fill: parent
        active: ui.pickerOpen
        sourceComponent: PickerSheet {}
    }
}
