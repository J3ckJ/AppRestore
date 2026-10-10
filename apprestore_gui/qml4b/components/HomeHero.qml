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
    signal link(string name)
    width: Theme.heroWidth

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
        column: root.st === "many" && String(root.view.number || 0).length >= 3
    }
    T {
        visible: root.hasTitle && !root.hasNumber
        token: "h1"
        text: root.view.title || ""
        lineHeightMode: Text.FixedHeight
        lineHeight: 84 * 0.96
        // CSS lets glyphs overflow a tight line box (negative half-leading);
        // Qt does not, so lift by that half-leading to match the concept.
        transform: Translate { y: (84 * 0.96 - 84 * 1.21) / 2 }
    }

    // lead
    Item { width: 1; height: 30; visible: lead.visible }
    T {
        id: lead
        visible: (root.view.lead || "") !== ""
        token: "lead"
        color: Theme.ink2
        width: Theme.leadWidth
        wrapMode: Text.WordWrap
        textFormat: Text.StyledText
        lineHeightMode: Text.FixedHeight
        lineHeight: 22 * 1.4
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
    Cta {
        id: cta
        visible: (root.view.cta || "") !== ""
        text: root.view.cta || ""
        secondary: !!root.view.ctaSecondary
        onClicked: root.primary()
    }
    Item { width: 1; height: 14; visible: hint.visible }
    InfoLine {
        id: hint
        visible: (root.view.hint || "") !== ""
        text: root.view.hint || ""
    }
    Item { width: 1; height: 16; visible: fine.visible }
    InfoLine {
        id: fine
        visible: (root.view.fine || "") !== ""
        text: root.view.fine || ""
        glyph: root.st === "signin" ? "lock" : "info"
        wrap: true
    }
    Item { width: 1; height: 30; visible: links.visible }
    Links {
        id: links
        visible: (root.view.links || []).length > 0
        items: root.view.links || []
        onActivated: function(name) { root.link(name) }
    }
}
