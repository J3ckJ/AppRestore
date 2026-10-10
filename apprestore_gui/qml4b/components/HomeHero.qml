import QtQuick
import "../theme"

// Left column of the home screen for every state of ui.home.
Column {
    id: root
    property var view: ({})
    readonly property string st: view.state || ""
    readonly property bool hasNumber: (view.word || "") !== ""
    readonly property bool hasTitle: (view.title || "") !== ""
    signal primary()
    signal secondary()
    signal link(string name)
    // heroWidth = min(620, W − padX − phoneW − rightMargin − 48) — задаёт Main
    width: Theme.heroWidth
    property alias primaryButton: cta

    function rich(text) {
        return (text || "").replace(/<b>/g, "<font color='" + Theme.ink + "'><b>").replace(/<\/b>/g, "</b></font>")
    }

    T { token: "over"; text: root.view.over || "" }

    // figure or headline
    Item { width: 1; height: root.hasNumber ? 20 : 22; visible: root.hasNumber || root.hasTitle }
    BigNumber {
        visible: root.hasNumber
        number: root.view.number || 0
        word: root.view.word || ""
        word2: root.view.word2 || ""
        small: root.st === "installing"
        maxWidth: root.width
        accessibleText: root.view.a11y || ""
    }
    T {
        visible: root.hasTitle && !root.hasNumber
        token: "h1"
        text: root.view.title || ""
        lineHeightMode: Text.FixedHeight
        lineHeight: Theme.display.lh
        height: lineCount * Theme.display.lh
        // CSS lets glyphs overflow a tight line box (negative half-leading);
        // Qt does not, so lift by that half-leading to match the concept.
        transform: Translate { y: (Theme.display.lh - Theme.display.size * 1.21) / 2 }
    }

    // lead
    Item { width: 1; height: 30; visible: lead.visible }
    T {
        id: lead
        visible: (root.view.lead || "") !== ""
        token: "lead"
        color: Theme.ink2
        width: Math.min(Theme.heroLeadWidth, root.width)
        wrapMode: Text.WordWrap
        textFormat: Text.StyledText
        lineHeightMode: Text.FixedHeight
        lineHeight: Theme.lead.lh
        text: root.rich(root.view.lead)
    }

    // numbered steps (disconnected)
    Item { width: 1; height: 30; visible: steps.visible }
    Column {
        id: steps
        visible: (root.view.steps || []).length > 0
        spacing: 16
        Repeater {
            model: root.view.steps || []
            Row {
                spacing: 16
                Rectangle {
                    width: 28; height: 28; radius: 14
                    color: "transparent"
                    border.width: 1.5
                    border.color: Theme.ink
                    T { anchors.centerIn: parent; token: "stepNum"; text: String(index + 1) }
                    anchors.verticalCenter: parent.verticalCenter
                }
                T { token: "step"; text: modelData; anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }

    // installing queue
    Item { width: 1; height: 28; visible: queue.visible }
    QueueList {
        id: queue
        visible: root.st === "installing"
        rows: root.view.queue || []
    }

    // big button
    Item { width: 1; height: 34; visible: cta.visible }
    Row {
        visible: cta.visible
        spacing: 9
        Cta {
            id: cta
            visible: (root.view.cta || "") !== ""
            text: root.view.cta || ""
            secondary: !!root.view.ctaSecondary
            onClicked: root.primary()
        }
        Cta {
            visible: (root.view.cta2 || "") !== ""
            text: root.view.cta2 || ""
            secondary: true
            onClicked: root.secondary()
        }
    }
    Item { width: 1; height: 14; visible: hint.visible }
    InfoLine {
        id: hint
        visible: (root.view.hint || "") !== ""
        text: root.view.hint || ""
        wrap: root.st === "store_mismatch"
    }
    Item { width: 1; height: 16; visible: fine.visible }
    InfoLine {
        id: fine
        visible: (root.view.fine || "") !== ""
        text: root.view.fine || ""
        glyph: root.st === "signin" ? "lock" : "info"
        color: root.st === "needs_component" ? Theme.ink2 : Theme.ink3Text
        wrap: true
        onLinkActivated: function(link) { root.link(link) }
    }
    Item { width: 1; height: 30; visible: links.visible }
    Links {
        id: links
        visible: (root.view.links || []).length > 0
        items: root.view.links || []
        onActivated: function(name) { root.link(name) }
    }
}
