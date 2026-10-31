import QtQuick 2.7
import Lomiri.Components 1.3
import 'components'

Page {
    id: page

    property string name
    property string path
    // Set for a photo. A PDF draws each page as it's opened.
    property string image: ''
    property int pages: 1
    property int current: 1
    property string shown: image
    property bool busy: false

    function show(number) {
        page.current = number
        page.busy = true
        root.call('preview', [page.path, number], function(reply) {
            if (!page) {  // Left the page before it was drawn.
                return
            }
            page.busy = false
            if (reply.ok) {
                page.shown = reply.result.image
            } else {
                root.showError(reply.error)
            }
        })
    }

    Component.onCompleted: {
        if (image === '') {
            show(1)
        }
    }

    header: PageHeader {
        title: page.name
        StyleHints {
            backgroundColor: root.ui.window
            dividerColor: 'transparent'
            foregroundColor: root.ui.text
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.ui.window
    }

    Image {
        anchors {
            top: page.header.bottom
            left: parent.left
            right: parent.right
            bottom: pager.top
            margins: units.gu(1)
        }
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        cache: false
        source: page.shown !== '' ? 'file://' + page.shown : ''
    }

    ActivityIndicator {
        anchors.centerIn: parent
        running: page.busy
        visible: running
    }

    Item {
        id: pager
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        height: page.pages > 1 ? units.gu(7) : 0
        visible: page.pages > 1

        Row {
            anchors.centerIn: parent
            spacing: units.gu(2)

            Pill {
                width: units.gu(5)
                iconName: 'previous'
                enabled: page.current > 1
                onClicked: page.show(page.current - 1)
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                color: root.ui.text
                text: i18n.tr('Page %1 of %2').arg(page.current).arg(page.pages)
            }
            Pill {
                width: units.gu(5)
                iconName: 'next'
                enabled: page.current < page.pages
                onClicked: page.show(page.current + 1)
            }
        }
    }
}
