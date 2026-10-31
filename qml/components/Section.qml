import QtQuick 2.7
import Lomiri.Components 1.3

// A heading with an optional line of explanation.
Column {
    property alias title: heading.text
    property alias subtitle: note.text

    spacing: units.gu(0.25)

    Label {
        id: heading
        width: parent.width
        font.weight: Font.Bold
        color: root.ui.text
        elide: Text.ElideRight
    }
    Label {
        id: note
        width: parent.width
        visible: text !== ''
        wrapMode: Text.WordWrap
        color: root.ui.dim
    }
}
