import QtQuick 2.7
import Lomiri.Components 1.3
import 'components'

Flickable {
    id: tab

    property bool shown: false
    property var checks: []
    property bool busy: false
    property bool done: false
    readonly property var office: root.office
    readonly property var installing: root.officeProgress

    function run() {
        if (root.printer === null) {
            return
        }
        tab.busy = true
        tab.done = true
        root.call('diagnose', [root.printer], function(reply) {
            tab.busy = false
            if (reply.ok) {
                tab.checks = reply.result
            } else {
                root.showError(reply.error)
            }
        })
    }

    function iconFor(state) {
        return state === 'ok' ? 'ok' : state === 'warn' ? 'info' : 'close'
    }

    function colorFor(state) {
        return state === 'ok' ? root.ui.good : state === 'warn' ? root.ui.warning : root.ui.dangerText
    }

    function installText() {
        var stage = tab.installing.stage === 'download' ? i18n.tr('Downloading') : i18n.tr('Unpacking')
        return '%1 %2%'.arg(stage).arg(Math.round(tab.installing.fraction * 100))
    }

    onShownChanged: {
        if (shown && !done) {
            run()
        }
    }

    contentHeight: column.height + units.gu(3)
    boundsBehavior: Flickable.StopAtBounds
    clip: true

    Column {
        id: column
        x: units.gu(2)
        y: units.gu(1)
        width: parent.width - units.gu(4)
        spacing: units.gu(1.5)

        Item {
            width: parent.width
            height: Math.max(heading.height, again.height)

            Section {
                id: heading
                anchors { left: parent.left; right: again.left; rightMargin: units.gu(1) }
                title: i18n.tr('Diagnostics')
                subtitle: i18n.tr('Checks the network, the printer and the scanner')
            }
            Pill {
                id: again
                anchors.right: parent.right
                width: units.gu(5)
                iconName: 'reload'
                busy: tab.busy
                enabled: !tab.busy
                onClicked: tab.run()
            }
        }

        Card {
            visible: tab.checks.length > 0

            Repeater {
                model: tab.checks
                delegate: CardRow {
                    Item {
                        width: parent.width
                        height: Math.max(units.gu(3), checkText.height)

                        Icon {
                            anchors.verticalCenter: parent.verticalCenter
                            width: units.gu(2.25)
                            height: width
                            name: tab.iconFor(modelData.state)
                            color: tab.colorFor(modelData.state)
                        }
                        Column {
                            id: checkText
                            anchors { left: parent.left; leftMargin: units.gu(3.5); right: parent.right }
                            Label {
                                width: parent.width
                                color: root.ui.text
                                text: modelData.title
                            }
                            Label {
                                width: parent.width
                                wrapMode: Text.WordWrap
                                textSize: Label.Small
                                color: root.ui.dim
                                text: modelData.detail
                            }
                        }
                    }
                }
            }
        }

        Section {
            width: parent.width
            title: i18n.tr('Office documents')
            subtitle: i18n.tr('LibrePrint keeps its own copy of LibreOffice to turn Word, Excel and PowerPoint files into pages.')
        }

        Card {
            CardRow {
                Column {
                    width: parent.width
                    spacing: units.gu(1)

                    Item {
                        width: parent.width
                        height: units.gu(5)

                        Column {
                            anchors {
                                left: parent.left
                                right: officeButton.left
                                rightMargin: units.gu(1)
                                verticalCenter: parent.verticalCenter
                            }
                            Label {
                                color: root.ui.text
                                text: tab.office ? i18n.tr('LibreOffice %1').arg(tab.office.version) : i18n.tr('LibreOffice')
                            }
                            Label {
                                width: parent.width
                                elide: Text.ElideRight
                                textSize: Label.Small
                                color: root.ui.dim
                                text: tab.installing ? tab.installText()
                                      : tab.office && tab.office.installed ? i18n.tr('Installed')
                                      : tab.office && !tab.office.available ? i18n.tr('Not available for this phone')
                                      : tab.office ? i18n.tr('%1 MB download').arg(tab.office.size) : ''
                            }
                        }
                        Pill {
                            id: officeButton
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                            kind: tab.office && tab.office.installed ? 'danger' : 'accent'
                            busy: tab.installing !== null
                            enabled: tab.office !== null && tab.office.available && tab.installing === null
                            text: tab.office && tab.office.installed ? i18n.tr('Remove') : i18n.tr('Install')
                            onClicked: tab.office.installed ? root.removeOffice() : root.installOffice()
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: units.gu(0.5)
                        radius: height / 2
                        color: root.ui.window
                        visible: tab.installing !== null

                        Rectangle {
                            height: parent.height
                            radius: parent.radius
                            color: root.ui.accent
                            width: tab.installing ? parent.width * tab.installing.fraction : 0
                        }
                    }
                }
            }
        }
    }
}
