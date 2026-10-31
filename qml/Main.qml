import QtQuick 2.7
import Lomiri.Components 1.3
import Lomiri.Content 1.3
import QtSystemInfo 5.0
import io.thp.pyotherside 1.4
import 'components'

MainView {
    id: root
    objectName: 'mainView'
    applicationName: 'libreprint.juujbyg'
    automaticOrientation: true
    theme: ThemeSettings { name: 'Lomiri.Components.Themes.SuruDark' }
    backgroundColor: ui.window

    width: units.gu(45)
    height: units.gu(75)

    // The PC app's colours.
    readonly property QtObject ui: QtObject {
        readonly property color window: '#2a2a2a'
        readonly property color card: '#3a3a3a'
        readonly property color pill: '#4d4d4d'
        readonly property color accent: '#c94212'
        readonly property color banner: '#c4152b'
        readonly property color bannerPill: '#d4525f'
        readonly property color dangerPill: '#5b3a3a'
        readonly property color dangerText: '#ff8585'
        readonly property color text: '#ffffff'
        readonly property color dim: '#a0a0a0'
        readonly property color good: '#8ff0a4'
        readonly property color warning: '#f8c15c'
        readonly property color gaugeEmpty: '#4a4a4a'
        readonly property color gaugeEdge: '#9a9a9a'
        readonly property int quick: 180
        readonly property int slow: 500
    }

    property var printer: null
    property var info: null
    property string failure: ''
    property var scanner: null
    property int tab: 0
    property var office: null
    property var officeProgress: null
    // While printing: the document, how far through it, and what's happening.
    property var progress: null
    readonly property bool printing: progress !== null
    property int preparing: 0
    property int readyDocuments: 0

    property int lastRequest: 0
    property var waiting: ({})
    property int lastKey: 0
    property bool polling: false
    property int checks: 0
    property bool cancelling: false
    property bool scannerChecked: false
    property bool testing: false
    // Names of the documents in the job being sent, for the queue on the Printer tab.
    property var batch: []
    property var incoming: null

    function call(name, args, callback) {
        var request = ++lastRequest
        waiting[request] = callback
        bridge.call('backend.run', [request, name, args])
    }

    function toast(message, isError) {
        toastLabel.text = message
        toastBox.isError = isError === true
        toastBox.shown = true
        toastTimer.interval = isError === true ? 7000 : 3500
        toastTimer.restart()
    }

    function showError(message) {
        toast(message, true)
    }

    function countDocuments(state) {
        var count = 0
        for (var i = 0; i < documentModel.count; ++i) {
            if (documentModel.get(i).state === state) {
                count++
            }
        }
        return count
    }

    function recount() {
        preparing = countDocuments('preparing')
        readyDocuments = countDocuments('ready')
    }

    function documentIndex(key) {
        for (var i = 0; i < documents.count; ++i) {
            if (documents.get(i).key === key) {
                return i
            }
        }
        return -1
    }

    function refresh(everything) {
        if (printer === null || polling) {
            return
        }
        polling = true
        var asked = printer
        // Ink and paper rarely change, and full answers are heavy work for a printer.
        var full = everything === true || info === null || (!printing && ++checks % 6 === 0)
        call(full ? 'status' : 'quick_status', [asked], function(reply) {
            polling = false
            if (asked !== printer) {
                return
            }
            if (reply.ok) {
                var first = info === null
                var latest = full ? reply.result : withUpdate(reply.result)
                // Replacing it rebuilds every list on screen, so only do that for a change.
                if (first || JSON.stringify(latest) !== JSON.stringify(info)) {
                    info = latest
                }
                failure = ''
                if (first) {
                    prepareAll()
                }
                findScanner()
            } else {
                failure = reply.error
                scannerChecked = false
                if (info !== null) {
                    // What it said last can't be trusted, least of all a queue that may be gone.
                    info = withUpdate({ state: 'offline', reasons: [], jobs: [] })
                }
            }
        })
    }

    function withUpdate(update) {
        var merged = JSON.parse(JSON.stringify(info))
        for (var name in update) {
            merged[name] = update[name]
        }
        return merged
    }

    function usePrinter(chosen) {
        printer = chosen
        info = null
        failure = ''
        scanner = null
        scannerChecked = false
        polling = false
        bridge.call('backend.remember', [chosen])
        refresh()
    }

    // Asked once the printer answers, and again after it has been away, because an
    // offline printer looks the same as one with no scanner.
    function findScanner() {
        if (scannerChecked || scanner !== null) {
            return
        }
        scannerChecked = true
        var asked = printer
        call('scanner', [asked.host], function(reply) {
            if (asked === printer && reply.ok) {
                scanner = reply.result
            }
        })
    }

    function queueRows() {
        if (progress === null) {
            return []
        }
        return batch.slice(progress.index).map(function(name, position) {
            var text = i18n.tr('Waiting')
            if (position === 0) {
                text = progress.stage === 'waiting' ? i18n.tr('Waiting for the printer')
                     : progress.pages > 0 && progress.page < progress.pages
                       ? i18n.tr('Preparing page %1 of %2').arg(progress.page).arg(progress.pages)
                       : i18n.tr('Sending')
            }
            return { name: name, text: text }
        })
    }

    function addFile(url) {
        var name = decodeURIComponent(String(url).split('/').pop())
        documents.append({
            key: ++lastKey, url: String(url), name: name, state: 'preparing',
            action: '', path: '', mime: '', pages: 0, thumbnail: '', message: ''
        })
        if (info !== null) {
            prepareDocument(lastKey)
        }
    }

    function prepareDocument(key) {
        var index = documentIndex(key)
        documents.setProperty(index, 'state', 'preparing')
        call('prepare', [documents.get(index).url, info], function(reply) {
            var row = documentIndex(key)
            if (row === -1) {
                return
            }
            if (!reply.ok) {
                documents.setProperty(row, 'message', reply.error)
                documents.setProperty(row, 'state', 'failed')
                return
            }
            var result = reply.result
            documents.setProperty(row, 'action', result.action)
            documents.setProperty(row, 'path', result.path)
            documents.setProperty(row, 'mime', result.mime)
            documents.setProperty(row, 'pages', result.pages || 0)
            documents.setProperty(row, 'thumbnail', result.thumbnail || '')
            documents.setProperty(row, 'state', result.action === 'refuse' || result.action === 'office-missing'
                                  ? 'blocked' : 'ready')
        })
    }

    function prepareAll() {
        for (var i = 0; i < documents.count; ++i) {
            prepareDocument(documents.get(i).key)
        }
    }

    function printDocuments(options) {
        var ready = []
        for (var i = 0; i < documents.count; ++i) {
            var row = documents.get(i)
            if (row.state === 'ready') {
                ready.push({ action: row.action, path: row.path, mime: row.mime, name: row.name })
            }
        }
        batch = ready.map(function(document) { return document.name })
        progress = { name: ready[0].name, index: 0, total: ready.length, stage: 'preparing', page: 0, pages: 0 }
        tab = 0
        call('print_documents', [printer, info, ready, options], function(reply) {
            progress = null
            cancelling = false
            if (!reply.ok) {
                showError(reply.error)
            } else if (reply.result.cancelled) {
                toast(i18n.tr('Printing cancelled'))
            } else {
                for (var j = documents.count - 1; j >= 0; --j) {
                    if (documents.get(j).state === 'ready') {
                        documents.remove(j)
                    }
                }
                toast(i18n.tr('Sent %1 document to the printer', 'Sent %1 documents to the printer', reply.result.sent).arg(reply.result.sent))
            }
            refresh(true)
        })
    }

    function cancelAll() {
        cancelling = true
        call('cancel_all', [printer], function(reply) {
            if (!printing) {
                cancelling = false
            }
            if (!reply.ok) {
                showError(reply.error)
            }
            refresh()
        })
    }

    function installOffice() {
        officeProgress = { stage: 'download', fraction: 0 }
        call('office_install', [], function(reply) {
            officeProgress = null
            if (reply.ok) {
                office = reply.result
                toast(i18n.tr('LibreOffice is ready'))
                for (var i = 0; i < documents.count; ++i) {
                    if (documents.get(i).action === 'office-missing') {
                        prepareDocument(documents.get(i).key)
                    }
                }
            } else {
                showError(reply.error)
            }
        })
    }

    function removeOffice() {
        call('office_remove', [], function(reply) {
            if (reply.ok) {
                office = reply.result
            }
        })
    }

    function takeItems(transfer) {
        for (var i = 0; i < transfer.items.length; ++i) {
            addFile(transfer.items[i].url)
        }
        if (transfer.items.length > 0) {
            tab = 1
        }
    }

    function receive(transfer) {
        if (transfer.state === ContentTransfer.Charged) {
            takeItems(transfer)
        } else {
            incoming = transfer
        }
    }

    ListModel {
        id: documentModel
        onCountChanged: root.recount()
        onDataChanged: root.recount()
    }

    Connections {
        target: ContentHub
        onImportRequested: root.receive(transfer)
        onShareRequested: root.receive(transfer)
    }

    Connections {
        target: root.incoming
        onStateChanged: {
            if (root.incoming && root.incoming.state === ContentTransfer.Charged) {
                root.takeItems(root.incoming)
                root.incoming = null
            }
        }
    }

    // A raster job is fed to the printer as it prints, and a locked phone suspends the app.
    ScreenSaver {
        screenSaverEnabled: !root.printing && root.officeProgress === null
    }

    Timer {
        interval: 8000
        repeat: true
        // Not during a job: the HP dropped off the network mid-job only once the app
        // started talking to it while sending. See CLAUDE.md.
        running: root.printer !== null && Qt.application.active && !root.printing && !root.testing
        onTriggered: root.refresh()
    }

    Rectangle {
        anchors.fill: parent
        color: root.ui.window
    }

    PageStack {
        id: pageHolder
        anchors.fill: parent
    }

    Rectangle {
        id: toastBox
        property bool shown: false
        property bool isError: false

        anchors {
            horizontalCenter: parent.horizontalCenter
            bottom: parent.bottom
            bottomMargin: shown ? units.gu(10) : units.gu(7)
        }
        width: Math.min(parent.width - units.gu(4), toastLabel.implicitWidth + units.gu(4))
        height: toastLabel.height + units.gu(2.5)
        radius: units.gu(1.5)
        color: isError ? root.ui.banner : '#1c1c1c'
        opacity: shown ? 1 : 0
        visible: opacity > 0
        z: 100

        Behavior on opacity {
            NumberAnimation { duration: root.ui.quick }
        }
        Behavior on anchors.bottomMargin {
            NumberAnimation { duration: root.ui.quick; easing.type: Easing.OutCubic }
        }

        Label {
            id: toastLabel
            anchors.centerIn: parent
            width: Math.min(implicitWidth, root.width - units.gu(8))
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: root.ui.text
        }
        MouseArea {
            anchors.fill: parent
            onClicked: toastBox.shown = false
        }
        Timer {
            id: toastTimer
            onTriggered: toastBox.shown = false
        }
    }

    Python {
        id: bridge

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../src/'))
            setHandler('reply', function(request, reply) {
                var callback = root.waiting[request]
                delete root.waiting[request]
                if (callback) {
                    callback(reply)
                }
            })
            setHandler('progress', function(update) {
                root.progress = update
            })
            setHandler('office', function(stage, fraction) {
                root.officeProgress = { stage: stage, fraction: fraction }
            })
            importModule('backend', function() {
                pageHolder.push(Qt.resolvedUrl('HomePage.qml'))
                root.call('office_state', [], function(reply) {
                    root.office = reply.ok ? reply.result : null
                })
                bridge.call('backend.startup', [], function(printers) {
                    if (printers.length > 0) {
                        root.usePrinter(printers[printers.length - 1])
                    } else {
                        pageHolder.push(Qt.resolvedUrl('PrintersPage.qml'))
                    }
                })
            })
        }

        onError: {
            console.log('python error: ' + traceback)
            root.showError(i18n.tr('The app hit an internal error.'))
        }
    }

    property alias documents: documentModel
    property alias python: bridge
    property alias stack: pageHolder
}
