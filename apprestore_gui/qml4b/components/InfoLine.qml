import QtQuick
import "../theme"

// .hint (one line, centred icon) or .fine (wrapping, icon on the first line).
Row {
    id: root
    property string text: ""
    property string glyph: "info"
    property bool wrap: false
    property int maxWidth: Theme.fineWidth
    spacing: wrap ? 8 : 7
    Glyph {
        name: root.glyph
        size: 14
        y: root.wrap ? 4 : Math.round((label.height - height) / 2)
    }
    T {
        id: label
        token: "hint"
        color: Theme.ink3
        text: root.text
        width: root.wrap ? root.maxWidth - 22 : implicitWidth
        wrapMode: root.wrap ? Text.WordWrap : Text.NoWrap
        lineHeightMode: root.wrap ? Text.FixedHeight : Text.ProportionalHeight
        lineHeight: root.wrap ? Math.round(14.5 * 1.5) : 1.0
    }
}
