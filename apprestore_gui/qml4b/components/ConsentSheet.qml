import QtQuick
import "../theme"

// Before a run (Ника 02 §5.2): how many apps get a new license on the Apple ID, and
// how much of the 5/24 h · 15 total limit is left (license_guard.read_counts,
// read again each time this sheet opens). Nothing starts until a choice.
Sheet {
    id: root
    objectName: "consentSheet"
    readonly property var c: ui.consent
    title: root.c.title || ""
    panelWidth: 600
    onCancel: ui.consentCancel()

    Column {
        id: col
        width: parent.width
        spacing: 0

        T {
            objectName: "consentLead"
            token: "lead"
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.c.lead || ""
        }
        Item { width: 1; height: 8 }
        T {
            token: "sheetSub"
            color: Theme.ink2
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.c.names || ""
        }
        Item { width: 1; height: 12 }
        T {
            objectName: "consentAbout"
            token: "sheetSub"
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.c.about || ""
        }
        Item { width: 1; height: 18 }
        Rectangle {
            width: parent.width
            height: limitText.height + 24
            radius: Theme.radiusRail
            color: Theme.card
            border.width: 1
            border.color: Theme.line
            T {
                id: limitText
                objectName: "consentLimit"
                x: 14; y: 12
                width: parent.width - 28
                wrapMode: Text.WordWrap
                token: "sheetSub"
                font.features: Theme.tnum
                text: root.c.limit || ""
            }
        }
        Item { width: 1; height: 12; visible: warn.visible }
        InfoLine {
            id: warn
            maxWidth: col.width
            objectName: "consentFine"
            visible: (root.c.fine || "") !== ""
            wrap: true
            glyph: "info"
            text: root.c.fine || ""
        }
        Item { width: 1; height: 8; visible: paid.visible }
        InfoLine {
            id: paid
            maxWidth: col.width
            visible: (root.c.paid || "") !== ""
            wrap: true
            glyph: "info"
            text: root.c.paid || ""
        }
        Item { width: 1; height: 8; visible: attempt.visible }
        InfoLine {
            id: attempt
            objectName: "consentAttempt"
            maxWidth: col.width
            visible: (root.c.attempt || "") !== ""
            wrap: true
            glyph: "info"
            text: root.c.attempt || ""
        }
        Item { width: 1; height: 24 }
        Row {
            spacing: 9
            Cta {
                objectName: "consentGo"
                text: root.c.go || ""
                onClicked: ui.consentContinue()
            }
            Cta {
                objectName: "consentOwned"
                secondary: true
                text: root.c.owned || ""
                enabledLook: !!root.c.ownedEnabled
                onClicked: ui.consentOwnedOnly()
            }
        }
    }
}
