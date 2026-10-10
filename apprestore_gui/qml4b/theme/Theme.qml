pragma Singleton
import QtQuick

// Design tokens of AppRestore 4b. Taken from the CSS of
// design/concepts/variant-4b*.html until Ника's spec arrives; the spec should
// only change values here. Light only: the concept has no dark variant.
QtObject {
    id: theme

    // -- platform font: SF on macOS, Segoe UI Variable / Segoe UI on Windows --
    readonly property bool isWindows: Qt.platform.os === "windows"
    readonly property bool isMac: Qt.platform.os === "osx" || Qt.platform.os === "macos"
    readonly property string fontFamily: isWindows
        ? (Qt.fontFamilies().indexOf("Segoe UI Variable Text") >= 0 ? "Segoe UI Variable Text" : "Segoe UI")
        : Qt.application.font.family
    // Big numbers and headlines (Segoe UI Variable Display on Windows 11).
    readonly property string displayFamily: isWindows
        ? (Qt.fontFamilies().indexOf("Segoe UI Variable Display") >= 0 ? "Segoe UI Variable Display" : "Segoe UI")
        : fontFamily

    // -- colors (concept :root) ----------------------------------------------
    readonly property color bg: "#ebeae6"
    readonly property color ink: "#121212"
    readonly property color ink2: "#61605c"
    readonly property color ink3: "#97958f"
    readonly property color line: "#d6d4ce"
    readonly property color card: "#ffffff"
    readonly property color accent: "#c45a2c"
    readonly property color ok: "#2f9e55"
    readonly property color off: "#a3a29d"
    readonly property color iosNew: "#0a84ff"
    readonly property color wall: "#ddd8cc"
    readonly property color sheet: "#ffffff"
    readonly property color soft: "#f3f2ee"
    readonly property color mark: "#f6ebe4"       // search hit
    readonly property color rowOn: "#fbf6f2"      // checked row
    readonly property color rowLine: "#ecebe6"
    readonly property color backdrop: Qt.rgba(20/255, 20/255, 18/255, 0.34)
    readonly property color track: "#d3d1ca"      // stage bars
    readonly property color capTrack: "#e2e0da"   // capacity bar
    readonly property color checkBorder: "#b8b6ae"
    readonly property color checkDisabledBorder: "#dcdad3"
    readonly property color goDisabled: "#d9d7d1"
    readonly property color iconPlaceholder: "#dcdad3"  // app without artwork: neutral, no letter
    readonly property color slotStroke: Qt.rgba(0, 0, 0, 0.32)
    readonly property color phoneFrame: "#1b1b1d"
    readonly property color phoneFrameEdge: "#38383b"
    readonly property color phoneBezel: "#0b0b0b"
    readonly property color phoneOff: "#1f1f21"
    readonly property color stepTodoBorder: "#c3c1ba"
    readonly property color stepOkBg: "#dcdad3"
    readonly property color stepLine: "#cfcdc6"
    readonly property color veil: Qt.rgba(0, 0, 0, 0.45)

    // -- type scale (px, weight, letter-spacing in em) ------------------------
    readonly property var type: ({
        brand:      { size: 17,   weight: 650, ls: -0.01 },
        pill:       { size: 14,   weight: 550, ls: 0 },
        over:       { size: 15,   weight: 700, ls: 0 },
        number:     { size: 300,  weight: 700, ls: -0.065 },
        numberSm:   { size: 190,  weight: 700, ls: -0.065 },
        numberWord: { size: 38,   weight: 650, ls: -0.025 },
        numberWordSm: { size: 32, weight: 650, ls: -0.025 },
        h1:         { size: 84,   weight: 700, ls: -0.045 },
        lead:       { size: 22,   weight: 400, ls: -0.005 },
        cta:        { size: 21,   weight: 650, ls: -0.01 },
        hint:       { size: 14.5, weight: 400, ls: 0 },
        link:       { size: 15,   weight: 500, ls: 0 },
        step:       { size: 21,   weight: 400, ls: -0.01 },
        stepNum:    { size: 14,   weight: 700, ls: 0 },
        queueName:  { size: 16.5, weight: 600, ls: 0 },
        queueSmall: { size: 13.5, weight: 450, ls: 0 },
        queueRight: { size: 14,   weight: 400, ls: 0 },
        stage:      { size: 12.5, weight: 600, ls: 0 },
        tileLabel:  { size: 12.5, weight: 500, ls: 0 },
        sheetTitle: { size: 30,   weight: 700, ls: -0.03 },
        sheetSub:   { size: 15,   weight: 500, ls: 0 },
        sheetLink:  { size: 15,   weight: 550, ls: 0 },
        rail:       { size: 15,   weight: 550, ls: 0 },
        railSmall:  { size: 12.5, weight: 500, ls: 0 },
        help:       { size: 13,   weight: 400, ls: 0 },
        search:     { size: 16,   weight: 400, ls: 0 },
        seg:        { size: 13.5, weight: 550, ls: 0 },
        group:      { size: 16,   weight: 700, ls: 0 },
        groupCount: { size: 14,   weight: 500, ls: 0 },
        row:        { size: 15,   weight: 550, ls: 0 },
        rowDev:     { size: 13.5, weight: 400, ls: 0 },
        size:       { size: 14,   weight: 400, ls: 0 },
        note:       { size: 13,   weight: 400, ls: 0 },
        total:      { size: 17,   weight: 700, ls: 0 },
        warn:       { size: 13.5, weight: 400, ls: 0 },
        go:         { size: 17,   weight: 650, ls: 0 },
        stepper:    { size: 14,   weight: 550, ls: 0 },
        scanCap:    { size: 15,   weight: 600, ls: 0 },
        found:      { size: 16,   weight: 400, ls: 0 },
        foundNum:   { size: 22,   weight: 700, ls: -0.02 },
        table:      { size: 18,   weight: 400, ls: 0 }
    })

    // -- layout (px) ---------------------------------------------------------
    readonly property int windowWidth: 1392
    readonly property int windowHeight: 852
    readonly property int padX: 76
    readonly property int topY: 52
    readonly property int heroTop: 150
    readonly property int heroWidth: 620
    readonly property int leadWidth: 540
    readonly property int fineWidth: 470
    readonly property int queueWidth: 560
    readonly property int buttonHeight: 68
    readonly property int ctaPadX: 46
    readonly property int linksGap: 30
    readonly property int brandIcon: 34
    readonly property int pillHeight: 36
    readonly property int phoneX: 806
    readonly property int phoneY: 128
    readonly property int phoneWidth: 430
    readonly property int tileIcon: 74
    readonly property int tileRowGap: 26
    readonly property int queueIcon: 40
    // picker sheet
    readonly property int sheetInsetX: 96
    readonly property int sheetInsetY: 34
    readonly property int sheetHead: 84
    readonly property int sheetFoot: 84
    readonly property int railWidth: 262
    readonly property int rowHeight: 46
    readonly property int groupHeight: 44
    readonly property int rowIcon: 34
    readonly property int listPadX: 28
    readonly property int capWidth: 220

    // -- radii ---------------------------------------------------------------
    readonly property int radiusCta: 18
    readonly property int radiusSheet: 18
    readonly property int radiusGo: 14
    readonly property int radiusSearch: 12
    readonly property int radiusRail: 10
    readonly property int radiusSeg: 10
    readonly property int radiusSegItem: 8
    readonly property int radiusCheck: 6
    readonly property int radiusRowIcon: 8
    readonly property int radiusQueueIcon: 9
    readonly property int radiusTile: 17

    function ls(token) { return type[token].ls * type[token].size }
}
