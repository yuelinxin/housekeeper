"""A folded details control shared by the removal and update confirmations."""

from gi.repository import Gtk, Pango

from housekeeper.i18n import _


class DetailsDisclosure(Gtk.Box):
    """A centered disclosure control with independently expanding detail text."""

    def __init__(self, preview):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.preview = preview
        self.toggle = Gtk.ToggleButton(halign=Gtk.Align.CENTER)
        self.toggle.add_css_class("flat")
        title = Gtk.Box(spacing=4)
        self.arrow = Gtk.Image(icon_name="pan-end-symbolic")
        title.append(self.arrow)
        title.append(Gtk.Label(label=_("Details")))
        self.toggle.set_child(title)
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=150,
            child=Gtk.ScrolledWindow(
                child=preview,
                max_content_height=240,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            ),
        )
        self.append(self.toggle)
        self.append(self.revealer)
        self.toggle.connect("toggled", self._toggled)
        self._toggled()

    def _toggled(self, *_args):
        expanded = self.toggle.get_active()
        self.arrow.set_from_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")
        self.revealer.set_reveal_child(expanded)
        # EXPANDED is a tristate GTK reads as an int; a Python bool fails its getter.
        self.toggle.update_state([Gtk.AccessibleState.EXPANDED], [1 if expanded else 0])


def details_label(text):
    """Selectable, wrapped disclosure text; a transaction line can be long."""
    return Gtk.Label(
        label=text,
        wrap=True,
        wrap_mode=Pango.WrapMode.WORD_CHAR,
        max_width_chars=48,
        selectable=True,
        xalign=0,
        margin_top=8,
    )
