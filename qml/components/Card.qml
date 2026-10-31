import QtQuick 2.7

// The rounded panel that groups rows, like a libadwaita boxed list.
Rectangle {
    default property alias content: rows.data
    property alias spacing: rows.spacing

    width: parent ? parent.width : 0
    height: rows.height
    radius: units.gu(1.5)
    color: root.ui.card

    Behavior on height {
        NumberAnimation { duration: root.ui.quick; easing.type: Easing.OutCubic }
    }

    Column {
        id: rows
        width: parent.width
    }
}
