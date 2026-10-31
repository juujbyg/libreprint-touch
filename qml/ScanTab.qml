import QtQuick 2.7
import Lomiri.Components 1.3
import Lomiri.Content 1.3
import 'components'

Flickable {
    id: tab

    property var pages: []
    property bool busy: false
    property var exported: []
    readonly property var scanner: root.scanner
    readonly property var resolutions: scanner ? scanner.dpis.filter(function(dpi) {
        return dpi === 150 || dpi === 300 || dpi === 600
    }) : []

    function usePages(reply) {
        tab.busy = false
        if (reply.ok) {
            tab.pages = reply.result
        } else {
            root.showError(reply.error)
        }
    }

    function scan() {
        tab.busy = true
        var dpi = tab.resolutions[resolutionChoice.current]
        root.call('scan_page', [tab.scanner, dpi, modeChoice.current === 0], function(reply) {
            tab.usePages(reply)
            if (reply.ok) {
                preview.currentIndex = tab.pages.length - 1
            }
        })
    }

    function save(kind) {
        root.call('export_scans', [kind], function(reply) {
            if (!reply.ok) {
                root.showError(reply.error)
                return
            }
            tab.exported = reply.result
            savePicker.contentType = kind === 'pdf' ? ContentType.Documents : ContentType.Pictures
            savePicker.visible = true
        })
    }

    onResolutionsChanged: {
        var preferred = tab.resolutions.indexOf(300)
        resolutionChoice.current = preferred === -1 ? 0 : preferred
    }

    Component.onCompleted: root.call('scans', [], tab.usePages)

    contentHeight: column.height + units.gu(3)
    boundsBehavior: Flickable.StopAtBounds
    clip: true

    Column {
        id: column
        x: units.gu(2)
        y: units.gu(1)
        width: parent.width - units.gu(4)
        spacing: units.gu(1.5)

        Rectangle {
            width: parent.width
            height: units.gu(46)
            radius: units.gu(1.5)
            color: root.ui.card

            ListView {
                id: preview
                anchors.fill: parent
                anchors.margins: units.gu(1)
                orientation: ListView.Horizontal
                snapMode: ListView.SnapOneItem
                highlightRangeMode: ListView.StrictlyEnforceRange
                clip: true
                model: tab.pages
                delegate: Image {
                    width: preview.width
                    height: preview.height
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                    sourceSize.height: units.gu(90)
                    source: 'file://' + modelData
                }
            }

            Label {
                anchors.centerIn: parent
                width: parent.width - units.gu(6)
                visible: tab.pages.length === 0 && !tab.busy
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: root.ui.dim
                text: tab.scanner
                      ? i18n.tr('Place the document face down on the glass and press Scan page. Scan several pages to save them as one PDF.')
                      : root.failure !== '' ? i18n.tr("Can't reach the printer. Scanning will come back when it does.")
                      : root.scannerChecked ? i18n.tr('No scanner found on this printer.')
                      : i18n.tr('Looking for the scanner…')
            }

            ActivityIndicator {
                anchors.centerIn: parent
                running: tab.busy
                visible: running
            }
        }

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: tab.pages.length > 0
            textSize: Label.Small
            color: root.ui.dim
            text: tab.pages.length > 1
                  ? i18n.tr('Page %1 of %2 · saving as PDF keeps them all').arg(preview.currentIndex + 1).arg(tab.pages.length)
                  : i18n.tr('1 page')
        }

        Card {
            CardRow {
                Item {
                    width: parent.width
                    height: units.gu(3)

                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.ui.text
                        text: i18n.tr('Device')
                    }
                    Label {
                        anchors {
                            right: parent.right
                            verticalCenter: parent.verticalCenter
                        }
                        width: Math.min(implicitWidth, parent.width - units.gu(9))
                        elide: Text.ElideRight
                        color: root.ui.dim
                        text: root.info ? root.info.model : ''
                    }
                }
            }
            CardRow {
                Column {
                    width: parent.width
                    spacing: units.gu(0.75)

                    Label {
                        color: root.ui.text
                        text: i18n.tr('Resolution')
                    }
                    Choice {
                        id: resolutionChoice
                        options: tab.resolutions.map(function(dpi) { return i18n.tr('%1 dpi').arg(dpi) })
                    }
                }
            }
            CardRow {
                Column {
                    width: parent.width
                    spacing: units.gu(0.75)

                    Label {
                        color: root.ui.text
                        text: i18n.tr('Mode')
                    }
                    Choice {
                        id: modeChoice
                        options: [i18n.tr('Colour'), i18n.tr('Greyscale')]
                    }
                }
            }
        }

        Pill {
            width: parent.width
            kind: 'accent'
            iconName: 'camera-symbolic'
            busy: tab.busy
            enabled: tab.scanner !== null && !tab.busy && !root.printing && root.failure === ''
            text: tab.busy ? i18n.tr('Scanning…') : i18n.tr('Scan page')
            onClicked: tab.scan()
        }

        Row {
            width: parent.width
            spacing: units.gu(1)
            visible: tab.pages.length > 0

            Pill {
                width: (parent.width - units.gu(1)) / 2
                iconName: 'save'
                enabled: !tab.busy
                text: i18n.tr('Save PDF')
                onClicked: tab.save('pdf')
            }
            Pill {
                width: (parent.width - units.gu(1)) / 2
                iconName: 'save'
                enabled: !tab.busy
                text: i18n.tr('Save JPEG')
                onClicked: tab.save('jpeg')
            }
        }

        Row {
            width: parent.width
            spacing: units.gu(1)
            visible: tab.pages.length > 0

            Pill {
                width: tab.pages.length > 1 ? (parent.width - units.gu(1)) / 2 : parent.width
                kind: 'danger'
                iconName: 'delete'
                enabled: !tab.busy
                text: i18n.tr('Remove page')
                onClicked: root.call('remove_scan', [tab.pages[preview.currentIndex]], tab.usePages)
            }
            Pill {
                width: (parent.width - units.gu(1)) / 2
                visible: tab.pages.length > 1
                kind: 'danger'
                enabled: !tab.busy
                text: i18n.tr('Remove all')
                onClicked: root.call('clear_scans', [], tab.usePages)
            }
        }
    }

    Component {
        id: exportItem
        ContentItem {}
    }

    ContentPeerPicker {
        id: savePicker
        parent: root
        anchors.fill: parent
        visible: false
        z: 50
        contentType: ContentType.Documents
        handler: ContentHandler.Destination
        onPeerSelected: {
            var transfer = peer.request()
            transfer.items = tab.exported.map(function(path) {
                return exportItem.createObject(tab, { url: 'file://' + path })
            })
            transfer.state = ContentTransfer.Charged
            visible = false
        }
        onCancelPressed: visible = false
    }
}
