import QtQuick 2.7
import Lomiri.Components 1.3
import 'components'

// Shows the photo with the page outlined. Drag a corner if it found the wrong edge.
Page {
    id: page

    property string path
    property var corners: []
    property bool found: false
    property bool busy: false
    readonly property var modes: ['colour', 'grey', 'text']
    signal finished(var pages)

    function finish(mode) {
        page.busy = true
        root.call('finish_photo', [page.path, page.corners, mode], function(reply) {
            if (!page) {  // Left the page before the picture was done.
                return
            }
            page.busy = false
            if (reply.ok) {
                page.finished(reply.result)
                root.stack.pop()
            } else {
                root.showError(reply.error)
            }
        })
    }

    function setCorner(index, x, y) {
        var next = page.corners.slice()
        next[index * 2] = Math.max(0, Math.min(1, x))
        next[index * 2 + 1] = Math.max(0, Math.min(1, y))
        page.corners = next
        outline.requestPaint()
    }

    Component.onDestruction: {
        if (!page.busy) {
            root.call('discard_photo', [page.path], function() {})
        }
    }

    header: PageHeader {
        title: i18n.tr('Adjust the page')
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

    Item {
        id: area
        anchors {
            top: page.header.bottom
            left: parent.left
            right: parent.right
            bottom: controls.top
            margins: units.gu(2.5)
        }

        Image {
            id: photo
            anchors.fill: parent
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
            sourceSize.height: units.gu(160)
            source: page.path !== '' ? 'file://' + page.path : ''

            // Where the picture sits inside the fitted area.
            readonly property real insetX: (width - paintedWidth) / 2
            readonly property real insetY: (height - paintedHeight) / 2

            onPaintedWidthChanged: outline.requestPaint()
            onPaintedHeightChanged: outline.requestPaint()
        }

        Canvas {
            id: outline
            anchors.fill: parent
            onPaint: {
                var context = getContext('2d')
                context.reset()
                if (page.corners.length !== 8 || photo.paintedWidth === 0) {
                    return
                }
                context.fillStyle = 'rgba(0, 0, 0, 0.45)'
                context.beginPath()
                context.rect(photo.insetX, photo.insetY, photo.paintedWidth, photo.paintedHeight)
                for (var i = 3; i >= 0; --i) {
                    var x = photo.insetX + page.corners[i * 2] * photo.paintedWidth
                    var y = photo.insetY + page.corners[i * 2 + 1] * photo.paintedHeight
                    if (i === 3) {
                        context.moveTo(x, y)
                    } else {
                        context.lineTo(x, y)
                    }
                }
                context.closePath()
                context.fill()
                context.strokeStyle = root.ui.accent
                context.lineWidth = units.dp(2)
                context.stroke()
            }
        }

        Repeater {
            model: 4

            Item {
                id: handle
                readonly property int corner: index
                width: units.gu(6)
                height: width
                x: photo.insetX + page.corners[corner * 2] * photo.paintedWidth - width / 2
                y: photo.insetY + page.corners[corner * 2 + 1] * photo.paintedHeight - height / 2

                Rectangle {
                    anchors.centerIn: parent
                    width: units.gu(3)
                    height: width
                    radius: width / 2
                    color: dragger.pressed ? root.ui.accent : '#ffffff'
                    border.color: root.ui.accent
                    border.width: units.dp(3)
                }

                MouseArea {
                    id: dragger
                    anchors.fill: parent
                    enabled: !page.busy
                    onPositionChanged: {
                        var spot = mapToItem(area, mouse.x, mouse.y)
                        page.setCorner(handle.corner, (spot.x - photo.insetX) / photo.paintedWidth,
                                       (spot.y - photo.insetY) / photo.paintedHeight)
                    }
                }
            }
        }

        ActivityIndicator {
            anchors.centerIn: parent
            running: page.busy
            visible: running
        }
    }

    Column {
        id: controls
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: units.gu(2)
        }
        spacing: units.gu(1)

        Label {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            color: root.ui.dim
            textSize: Label.Small
            text: page.found ? i18n.tr('Check the corners, then add the page.')
                             : i18n.tr("Couldn't find the edges. Drag the corners to the page.")
        }
        Choice {
            id: modeChoice
            options: [i18n.tr('Colour'), i18n.tr('Greyscale'), i18n.tr('Text')]
        }
        Pill {
            width: parent.width
            kind: 'accent'
            iconName: 'ok'
            busy: page.busy
            enabled: !page.busy
            text: page.busy ? i18n.tr('Cleaning up…') : i18n.tr('Add page')
            onClicked: page.finish(page.modes[modeChoice.current])
        }
        Pill {
            width: parent.width
            enabled: !page.busy
            text: i18n.tr('Keep the photo as it is')
            onClicked: page.finish('original')
        }
    }
}
