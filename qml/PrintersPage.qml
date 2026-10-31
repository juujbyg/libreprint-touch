import QtQuick 2.7
import QtQuick.Layouts 1.3
import Lomiri.Components 1.3
import 'components'

Page {
    id: page
    property bool searching: false
    property var savedPrinters: []
    property var foundPrinters: []

    function refresh() {
        root.python.call('backend.saved', [], function(printers) {
            page.savedPrinters = printers
        })
        page.searching = true
        root.call('find', [], function(reply) {
            if (!page) {  // Left the page while searching.
                return
            }
            page.searching = false
            page.foundPrinters = reply.ok ? reply.result : []
        })
    }

    function choose(printer) {
        root.usePrinter(printer)
        root.stack.pop()
    }

    Component.onCompleted: refresh()

    header: PageHeader {
        title: i18n.tr('Printers')
        StyleHints {
            backgroundColor: root.ui.window
            dividerColor: 'transparent'
            foregroundColor: root.ui.text
        }
        trailingActionBar.actions: [
            Action {
                iconName: 'reload'
                text: i18n.tr('Search again')
                enabled: !page.searching
                onTriggered: page.refresh()
            }
        ]
    }

    Rectangle {
        anchors.fill: parent
        color: root.ui.window
    }

    Flickable {
        anchors {
            top: page.header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        contentHeight: column.height
        clip: true

        Column {
            id: column
            width: parent.width

            ListItem {
                visible: page.savedPrinters.length > 0
                height: visible ? units.gu(4) : 0
                divider.visible: false
                Label {
                    anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                    text: i18n.tr('Your printers')
                    font.bold: true
                }
            }

            Repeater {
                model: page.savedPrinters
                delegate: ListItem {
                    height: savedLayout.height
                    onClicked: page.choose(modelData)
                    leadingActions: ListItemActions {
                        actions: [
                            Action {
                                iconName: 'delete'
                                text: i18n.tr('Forget')
                                onTriggered: root.python.call('backend.forget', [modelData.host], function(printers) {
                                    page.savedPrinters = printers
                                })
                            }
                        ]
                    }
                    ListItemLayout {
                        id: savedLayout
                        title.text: modelData.name
                        subtitle.text: modelData.host
                        Icon { name: 'next'; SlotsLayout.position: SlotsLayout.Last; width: units.gu(2) }
                    }
                }
            }

            ListItem {
                height: units.gu(4)
                divider.visible: false
                Label {
                    anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                    text: page.searching ? i18n.tr('Searching your network…') : i18n.tr('Found on your network')
                    font.bold: true
                }
                ActivityIndicator {
                    anchors { right: parent.right; rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                    running: page.searching
                }
            }

            Repeater {
                model: page.foundPrinters
                delegate: ListItem {
                    height: foundLayout.height
                    onClicked: page.choose(modelData)
                    ListItemLayout {
                        id: foundLayout
                        title.text: modelData.name
                        subtitle.text: modelData.host
                        Icon { name: 'next'; SlotsLayout.position: SlotsLayout.Last; width: units.gu(2) }
                    }
                }
            }

            ListItem {
                visible: !page.searching && page.foundPrinters.length === 0
                height: visible ? noneLabel.height + units.gu(2) : 0
                divider.visible: false
                Label {
                    id: noneLabel
                    anchors { left: parent.left; right: parent.right; margins: units.gu(2); verticalCenter: parent.verticalCenter }
                    text: i18n.tr('Nothing new found. Check that the printer is on and on the same Wi-Fi network.')
                    wrapMode: Text.WordWrap
                }
            }

            Item { width: 1; height: units.gu(2) }

            Column {
                width: parent.width - units.gu(4)
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: units.gu(1)

                Label {
                    width: parent.width
                    text: i18n.tr('Add by address')
                    font.bold: true
                }
                TextField {
                    id: address
                    width: parent.width
                    placeholderText: i18n.tr('For example 192.168.1.20')
                    inputMethodHints: Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                }
                Pill {
                    id: addButton
                    width: parent.width
                    kind: 'accent'
                    text: i18n.tr('Connect')
                    onClicked: {
                        enabled = false
                        busy = true
                        root.call('connect', [address.text], function(reply) {
                            addButton.enabled = true
                            addButton.busy = false
                            if (reply.ok) {
                                address.text = ''
                                page.choose(reply.result)
                            } else {
                                root.showError(reply.error)
                            }
                        })
                    }
                }
            }

            Item { width: 1; height: units.gu(3) }
        }
    }
}
