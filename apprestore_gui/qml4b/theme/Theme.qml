// Theme.qml — токены AppRestore 4b. Синглтон: в qmldir — "singleton Theme 1.0 Theme.qml".
// Источник: design/spec/tokens.json (из CSS концептов 4b). Значения — логические px.
pragma Singleton
import QtQuick

QtObject {
    readonly property bool isMac: Qt.platform.os === "osx" || Qt.platform.os === "macos"
    // ui4b: «--ui4b-as-windows» only for offscreen screenshots next to variant-4b-missing-windows.png
    readonly property bool isWin: Qt.platform.os === "windows" || Qt.application.arguments.indexOf("--ui4b-as-windows") >= 0

    // ── Цвета
    readonly property color bg: "#EBEAE6"
    readonly property color card: "#FFFFFF"
    readonly property color surfaceSoft: "#F3F2EE"
    readonly property color ink: "#121212"
    readonly property color ink2: "#61605C"
    readonly property color ink3: "#97958F"      // только декоративное/крупное (2.49:1 на bg)
    readonly property color ink3Text: "#77756F"  // мелкий вторичный текст (3.83:1 на bg, 4.61:1 на белом)
    readonly property color line: "#D6D4CE"
    readonly property color lineSoft: "#ECEBE6"
    readonly property color track: "#D3D1CA"
    readonly property color accent: "#C45A2C"
    readonly property color accentHover: "#B35128"
    readonly property color accentPressed: "#A04824"
    readonly property color accentDisabled: "#D9D7D1"
    readonly property color accentTint: "#F6EBE4"
    readonly property color ok: "#2F9E55"
    readonly property color okText: "#217A40"
    readonly property color off: "#A3A29D"
    readonly property color iosNew: "#0A84FF"
    readonly property color wall: "#DDD8CC"
    readonly property color screenOff: "#1F1F21"
    readonly property color phoneFrame: "#1B1B1D"
    readonly property color phoneFrameEdge: "#38383B"
    readonly property color phoneBezel: "#0B0B0B"
    readonly property color slotDash: Qt.rgba(0, 0, 0, 0.32)
    readonly property color veil: Qt.rgba(0, 0, 0, 0.45)
    readonly property color backdrop: Qt.rgba(20/255, 20/255, 18/255, 0.34)
    readonly property color focusRing: ink

    // ── Шрифты (Inter положить в бандл как запасной)
    readonly property string fontText: isMac ? ".AppleSystemUIFont" : (isWin ? "Segoe UI Variable Text" : "Inter")
    readonly property string fontDisplay: isMac ? ".AppleSystemUIFont" : (isWin ? "Segoe UI Variable Display" : "Inter")
    readonly property var tnum: ({ "tnum": 1 })  // font.features (Qt ≥ 6.6)

    // ── Типографика: size / weight / lineHeight(px) / letterSpacing(px)
    readonly property var numeralXL:  ({ size: 300, weight: 700, lh: 216, ls: -19.5 })
    readonly property var numeralL:   ({ size: 250, weight: 700, lh: 180, ls: -16.25 })
    readonly property var numeralM:   ({ size: 190, weight: 700, lh: 137, ls: -12.35 })
    readonly property var numeralCaption:  ({ size: 38, weight: 650, lh: 40, ls: -0.95 })
    readonly property var numeralCaptionM: ({ size: 32, weight: 650, lh: 34, ls: -0.8 })
    readonly property var display:    ({ size: 84, weight: 700, lh: 81, ls: -3.78 })
    readonly property var lead:       ({ size: 22, weight: 400, lh: 31, ls: -0.11 })
    readonly property var step:       ({ size: 21, weight: 400, lh: 28, ls: -0.21 })
    readonly property var button:     ({ size: 21, weight: 650, lh: 26, ls: -0.21 })
    readonly property var brand:      ({ size: 17, weight: 650, lh: 22, ls: -0.17 })
    readonly property var rowTitle:   ({ size: 16.5, weight: 600, lh: 22, ls: 0 })
    readonly property var overline:   ({ size: 15, weight: 700, lh: 20, ls: 0 })
    readonly property var link:       ({ size: 15, weight: 500, lh: 20, ls: 0 })
    readonly property var fine:       ({ size: 14.5, weight: 400, lh: 21.75, ls: 0 })
    readonly property var pill:       ({ size: 14, weight: 550, lh: 18, ls: 0 })
    readonly property var rowMeta:    ({ size: 13.5, weight: 450, lh: 18, ls: 0 })
    readonly property var stageLabel: ({ size: 12.5, weight: 600, lh: 16, ls: 0 })
    readonly property var phoneLabel: ({ size: 12.5, weight: 500, lh: 16, ls: 0 })

    // ── Отступы, радиусы, линии, размеры
    readonly property int padX: 76
    readonly property int heroTop: 150
    readonly property int headerTop: 52
    readonly property int heroWidth: 620
    readonly property int ctaHeight: 68
    readonly property int ctaPadX: 46
    readonly property int ctaRadius: isWin ? 8 : 18
    readonly property int pillHeight: 36
    readonly property int pillDot: 9
    readonly property int tileRadius: 17
    readonly property int phoneW: 430
    readonly property int phoneIcon: 74
    readonly property int phoneRightMargin: 156
    readonly property int phoneTop: 128
    readonly property real hair: 1
    readonly property real slotDashWidth: 2
    readonly property var slotDashPattern: [3, 3]   // в единицах толщины линии
    readonly property real ringWidth: 2.5
    readonly property int restoreAllMax: 12         // параметр: «Вернуть все N» vs «Выбрать и вернуть»

    // ── Движение
    readonly property int durFast: 150
    readonly property int durBase: 220
    readonly property int durSlow: 320
    readonly property int durSlotFill: 240
    readonly property int durCrossfade: 180
    readonly property int easeOut: Easing.OutCubic
    readonly property int easeInOut: Easing.InOutCubic

    // ═════════════════════════════════════════════════════════════════════════
    // ui4b: дополнения Димы. Выше — Theme.qml Ники без изменений (кроме isWin
    // для скриншотов). Ниже то, чего в части 1 спеки нет (окно «Что вернуть»,
    // онбординг, вход) — значения из CSS концептов и tokens.json; части 2–3
    // спеки заменят их. Старые имена компонентов сведены к токенам Ники.
    // ═════════════════════════════════════════════════════════════════════════

    // Шрифт: если системного нет (Linux, CI), Qt сам возьмёт запасной.
    readonly property string fontFamily: fontText
    readonly property string displayFamily: fontDisplay

    // Размеры из tokens.json, которых нет в Theme.qml части 1
    readonly property int brandIcon: 34
    readonly property int rowIconSize: 40      // иконка в очереди
    readonly property int rowIconRadius: 9
    readonly property int stageBar: 5
    readonly property int barRadius: 3
    readonly property int newDot: 6
    readonly property int pieBox: 44
    readonly property int heroLeadWidth: 540
    readonly property int fineWidth: 470
    readonly property int queueWidth: 560
    readonly property int linksGap: 30
    readonly property int windowMinWidth: 1180
    readonly property int windowMinHeight: 760
    readonly property int windowWidth: 1392    // окно макета
    readonly property int windowHeight: 852

    // Окно «Что вернуть», онбординг, вход (CSS концептов; ждём части 2–3)
    readonly property color sheet: card
    readonly property color soft: surfaceSoft
    readonly property color mark: accentTint          // подсветка найденного
    readonly property color rowOn: "#FBF6F2"          // tokens.json rowSelected
    readonly property color rowLine: lineSoft
    readonly property color capTrack: "#E2E0DA"
    readonly property color checkBorder: "#B8B6AE"
    readonly property color checkDisabledBorder: "#DCDAD3"  // tokens.json trackAlt
    readonly property color goDisabled: accentDisabled
    readonly property color iconPlaceholder: track    // спека §2.4: без иконки — #D3D1CA, без буквы
    readonly property color slotStroke: slotDash
    readonly property color phoneOff: screenOff
    readonly property color stepTodoBorder: "#C3C1BA"
    readonly property color stepOkBg: "#DCDAD3"
    readonly property color stepLine: "#CFCDC6"

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
    readonly property int radiusSheet: 18
    readonly property int radiusGo: 14
    readonly property int radiusSearch: 12
    readonly property int radiusRail: 10
    readonly property int radiusSeg: 10
    readonly property int radiusSegItem: 8
    readonly property int radiusCheck: 6
    readonly property int radiusRowIcon: 8

    // Цвета, которые раньше были литералами в компонентах (вне Theme цветов нет)
    readonly property color onAccent: "#FFFFFF"             // текст/галочка на тёмном и accent
    readonly property color focusRingInner: "#FFFFFF"       // tokens.json
    readonly property color secondaryPressed: "#E6E4DE"     // спека §1.1 [предл.]
    readonly property color iconHairline: Qt.rgba(0, 0, 0, 0.1)
    readonly property color sheetHairline: Qt.rgba(0, 0, 0, 0.08)
    readonly property color progressInk: "#FFFFFF"          // кольцо и сектор поверх вуали
    readonly property color island: "#000000"               // tokens.json
    readonly property color batteryStroke: Qt.rgba(0, 0, 0, 0.75)
    readonly property color pageDotOn: Qt.rgba(0, 0, 0, 0.7)
    readonly property color pageDotOff: Qt.rgba(0, 0, 0, 0.22)
    readonly property color iosDim: Qt.rgba(0, 0, 0, 0.3)   // затемнение под системным алертом
    readonly property color iosAlertBg: "#F4F4F2"
    readonly property color iosAlertInk: "#111111"
    readonly property color iosAlertText: "#333333"
    readonly property color iosAlertLine: "#D3D3D0"
    readonly property color iosAlertButton: "#0A6FE0"

    // Старые имена главного экрана → токены Ники
    readonly property int topY: headerTop
    readonly property int buttonHeight: ctaHeight
    readonly property int radiusCta: ctaRadius
    readonly property int radiusTile: tileRadius
    readonly property int radiusQueueIcon: rowIconRadius
    readonly property int tileIcon: phoneIcon
    readonly property int phoneWidth: phoneW
    readonly property int phoneY: phoneTop
    readonly property int queueIcon: rowIconSize
    readonly property int leadWidth: heroLeadWidth
    readonly property int tileRowGap: 26

    // Стили текста для <T token>: стили Ники по имени + стили окон частей 2–3.
    // ls здесь в px, как у Ники.
    readonly property var extraStyles: ({
        stepNum:    { size: 14,   weight: 700, ls: 0 },
        numeralCaptionCol: { size: 40, weight: 650, ls: -1.0 },  // подпись под 3-значной цифрой (picker.html .num.col)
        stepNum12:  { size: 12,   weight: 700, ls: 0 },
        queueRight: { size: 14,   weight: 400, ls: 0 },
        sheetTitle: { size: 30,   weight: 700, ls: -0.9 },
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
        foundNum:   { size: 22,   weight: 700, ls: -0.44 },
        table:      { size: 18,   weight: 400, ls: 0 }
    })
    readonly property var specStyles: ({
        numeralXL: numeralXL, numeralL: numeralL, numeralM: numeralM,
        numeralCaption: numeralCaption, numeralCaptionM: numeralCaptionM,
        display: display, lead: lead, step: step, button: button, brand: brand,
        rowTitle: rowTitle, overline: overline, link: link, fine: fine, pill: pill,
        rowMeta: rowMeta, stageLabel: stageLabel, phoneLabel: phoneLabel
    })
    // Прежние имена токенов текста → стили Ники
    readonly property var styleAlias: ({
        number: "numeralXL", numberSm: "numeralM", numberWord: "numeralCaption",
        numberWordSm: "numeralCaptionM", h1: "display", cta: "button", hint: "fine",
        over: "overline", queueName: "rowTitle", queueSmall: "rowMeta", stage: "stageLabel",
        tileLabel: "phoneLabel"
    })
    function style(name) {
        var n = styleAlias[name] || name
        return specStyles[n] || extraStyles[n] || lead
    }
    function isDisplay(name) {
        var n = styleAlias[name] || name
        return n === "display" || n.indexOf("numeral") === 0
    }
}
