import QtQuick
import "../theme"

// .hint (one line, centred icon) or .fine (wrapping, icon on the first line).
Row {
    id: root
    property string text: ""
    property string glyph: "info"
    property bool wrap: false
    property int maxWidth: Theme.fineWidth
    property color color: Theme.ink3Text   // спека §5: ink3 мелким не проходит контраст
    signal linkActivated(string link)
    spacing: wrap ? 8 : 7
    Glyph {
        Accessible.ignored: true
        name: root.glyph
        size: 14
        y: root.wrap ? 4 : Math.round((label.height - height) / 2)
    }
    T {
        id: label
        token: "hint"
        color: root.color
        text: root.text
        textFormat: root.text.indexOf("<") >= 0 ? Text.StyledText : Text.PlainText
        linkColor: Theme.ink
        onLinkActivated: function(link) { root.linkActivated(link) }
        width: root.wrap ? root.maxWidth - 22 : implicitWidth
        wrapMode: root.wrap ? Text.WordWrap : Text.NoWrap
        lineHeightMode: root.wrap ? Text.FixedHeight : Text.ProportionalHeight
        lineHeight: root.wrap ? Theme.fine.lh : 1.0
    }
}
