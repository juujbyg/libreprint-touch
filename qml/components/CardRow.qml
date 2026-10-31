import QtQuick 2.7

// A row inside a Card, with a line above it unless it comes first.
Item {
    default property alias content: holder.data
    property int padding: units.gu(1.5)

    width: parent ? parent.width : 0
    height: Math.max(units.gu(6.5), holder.childrenRect.height + 2 * padding)

    Rectangle {
        width: parent.width
        height: units.dp(1)
        color: root.ui.window
        visible: parent.Positioner.index > 0
    }

    Item {
        id: holder
        anchors {
            left: parent.left
            right: parent.right
            margins: units.gu(2)
            verticalCenter: parent.verticalCenter
        }
        height: childrenRect.height
    }
}
