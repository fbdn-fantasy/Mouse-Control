"""Original mouse meshes, perspective projection and an interactive GTK viewport."""

import math
from dataclasses import dataclass

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk


# Sections from nose to palm, with left width, right width, edge and crown height.
# The G502 has a raised spine, an asymmetric rail and a separate thumb rest.
_ROUND = (
    (.25, .25, .10, .23), (.49, .49, .17, .36), (.56, .56, .22, .46),
    (.58, .58, .24, .54), (.58, .58, .25, .62), (.59, .59, .26, .68),
    (.60, .60, .25, .71), (.59, .59, .23, .70), (.56, .56, .19, .63),
    (.46, .46, .15, .50), (.25, .25, .10, .32), (.06, .06, .07, .15),
)
_ANGULAR = (
    (.40, .32, .12, .25), (.51, .49, .20, .36), (.55, .53, .25, .44),
    (.59, .55, .28, .51), (.57, .55, .30, .59), (.54, .55, .31, .67),
    (.54, .58, .31, .77), (.56, .61, .29, .81), (.56, .63, .25, .77),
    (.53, .58, .20, .63), (.41, .42, .14, .45), (.16, .13, .07, .20),
)
_COMMON = ("Primary click", "Secondary click", "Wheel click", "Thumb back", "Thumb forward")
_NAMES = {
    "g305": _COMMON + ("DPI button",),
    "g502": _COMMON + ("Thumb DPI shift", "DPI down", "DPI up", "Profile button",
                       "Wheel tilt right", "Wheel tilt left"),
    "generic": _COMMON[:3],
    "g203": _COMMON + ("DPI button",),
    "pro": _COMMON + ("Right side back", "Right side forward", "Underside DPI"),
    "superlight": _COMMON,
    "master": _COMMON,
    "vertical": _COMMON,
    "trackball": _COMMON[:3],
    "g600": _COMMON[:3] + ("Wheel tilt left", "Wheel tilt right", "Ring finger shift", "Front top key", "Rear top key")
            + tuple(f"Thumb key G{i}" for i in range(9, 21)),
}
_NAMES["g502wireless"] = _NAMES["g502"]
_NAMES["g300"] = _COMMON[:3] + ("Left shoulder", "Left shoulder", "Right shoulder",
                                  "Right shoulder", "Rear center key", "Front center key")
for family in ("ergo6", "wired6", "diamond6"):
    _NAMES[family] = _NAMES["g203"]
for family in ("compact", "diamond5", "dual"):
    _NAMES[family] = _COMMON
_NAMES["cluster"] = _COMMON[:3]  # Side-key ordering depends on the exact onboard layout.
_HOME_YAW = .78
_HOME_ELEVATION = 1.00


def model_key(model):
    name = str(model).lower()
    if name in _NAMES:
        return name
    if "g600" in name or model == 0xC24A:
        return "g600"
    if "g300" in name or model == 0xC246:
        return "g300"
    if any(key in name for key in ("g604", "g602", "g700")):
        return "cluster"
    if any(k in name for k in ("trackball", "mx ergo", "m570", "m575")):
        return "trackball"
    if "vertical" in name or "lift" in name:
        return "vertical"
    if "mx master" in name:
        return "master"
    if any(key in name for key in ("anywhere", "m585", "m590", "m720")):
        return "compact"
    if "superlight" in name or "superstrike" in name:
        return "superlight"
    if model in (0xC088, 0x4079) or any(k in name for k in ("pro wireless", "pro 2 lightspeed", "pro gaming mouse wireless")):
        return "pro"
    if "g900" in name or "g903" in name:
        return "dual"
    if "g302" in name or "g303" in name:
        return "diamond5" if "shroud" in name else "diamond6"
    if any(key in name for key in ("g309", "g403", "g603", "g703", "g705")):
        return "wired6" if "g403" in name and "wireless" not in name else "ergo6"
    if "pro gaming mouse" in name or "g pro hero" in name:
        return "g203"
    if model in (0xC084, 0xC092, 0xC09D) or any(k in name for k in ("g203", "g102", "g103")):
        return "g203"
    if model in (0x4074, 0xC53F) or "g305" in name or "g304" in name:
        return "g305"
    if model in (0xC08D, 0x407F) or ("g502" in name and any(k in name for k in ("wireless", "lightspeed", "plus"))):
        return "g502wireless"
    if model == 0xC08B or "g502" in name:
        return "g502"
    return "generic"


@dataclass(frozen=True)
class Face:
    vertices: tuple
    color: tuple
    button: int
    light: float


def make_mesh(model):
    """Return solid faces and the centers of the physical button targets."""
    kind = model_key(model)
    angular = kind in ("g502", "g502wireless")
    sections = _ANGULAR if angular else _ROUND
    length = 2.53 if angular else (2.4 if kind in ("pro", "superlight", "master", "vertical") else 2.22)
    faces = []
    anchors = {}
    shell = (.20, .245, .29)
    clicks = (.31, .365, .415)
    trim = (.10, .14, .18)
    rubber = (.155, .185, .215)
    metal = (.38, .44, .49)
    accent = (.12, .53, .63)

    def surface(t, u):
        pos = min(max(t, 0), 1) * (len(sections) - 1)
        i = min(int(pos), len(sections) - 2)
        a, b = sections[i:i + 2]
        left, right, edge, rise = [lo + (hi - lo) * (pos - i) for lo, hi in zip(a, b)]
        crown = math.sqrt(max(0, 1 - abs(u) ** (2.0 if angular else 2.7)))
        return (u * (left if u < 0 else right), (t - .5) * length, edge + (rise - edge) * crown)

    def face(vertices, color, button=-1):
        p, q, r = vertices[:3]
        a = tuple(v - w for v, w in zip(q, p))
        b = tuple(v - w for v, w in zip(r, p))
        nx, ny, nz = a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]
        mag = math.sqrt(nx*nx + ny*ny + nz*nz) or 1
        diffuse = (-.45*nx - .4*ny + .8*nz) / mag
        faces.append(Face(tuple(vertices), color, button, .80 + .26 * diffuse))

    ts = (0, .07, .13, .17, .24, .25, .31, .36, .39, .42, .45, .49, .55, .59,
          .65, .72, .79, .86, .92, .97, 1)
    us = (-1, -.95, -.79, -.75, -.70, -.54, -.34, -.14, -.025, .025, .16, .38, .6, .82, .94, 1)
    def deck(t, u):
        seam = .40 + .10 * abs(u) if not angular else .36 + .16 * abs(u)
        mapped = t * seam / .45 if t <= .45 else seam + (t - .45) * (1 - seam) / .55
        return surface(mapped, u)

    for t0, t1 in zip(ts, ts[1:]):
        for u0, u1 in zip(us, us[1:]):
            u, t = (u0 + u1) / 2, (t0 + t1) / 2
            button = -1
            color = shell
            if t < .45:
                button = 0 if u < 0 else 1
                color = clicks
                if abs(u) < .026 or (angular and u < -.70):
                    color, button = trim, -1
                if angular and u < -.75 and .13 < t < .39 and not .24 < t < .25:
                    color, button = metal, (7 if t < .24 else 6)
            if angular and t > .55 and (u < -.75 or u > .82):
                color = rubber
            if kind == "g600" and t < .5 and u > .60:
                color, button = (.25, .30, .35), 5
            face((deck(t0, u0), deck(t0, u1), deck(t1, u1), deck(t1, u0)), color, button)

        for side in (-1, 1):
            top1, top2 = deck(t0, side), deck(t1, side)
            x1, y1, z1 = top1
            x2, y2, z2 = top2
            def foot(t, x):
                wing = .25 * max(0, 1 - abs(t - .52) / .35) if (angular or kind == "master") and side < 0 else 0
                return side * (abs(x) * .94 + wing)
            f1, f2 = foot(t0, x1), foot(t1, x2)
            bottom1, bottom2 = (f1, y1, .055), (f2, y2, .055)
            vertices = (top1, top2, bottom2, bottom1)
            face(vertices if side < 0 else vertices[::-1], rubber)
            face((bottom1, bottom2, (f2*.96, y2, 0), (f1*.96, y1, 0)), trim)
            face(((0,y1,0), (0,y2,0), (f2*.96,y2,0), (f1*.96,y1,0)), trim)
    for t, reverse in ((0, False), (1, True)):
        lip = [surface(t, u) for u in us]
        for p, q in zip(lip, lip[1:]):
            end = (p, q, (q[0], q[1], .01), (p[0], p[1], .01))
            face(end[::-1] if reverse else end, trim)
    for slot, u in ((0, -.37), (1, .46)):
        anchors[slot] = surface(.145 if angular else .18, u)

    def side_pad(slot, t, half_length, z, half_height, side=-1):
        # The eight beveled corners keep each pad distinct at grazing angles.
        corners = ((-1,.25),(-.83,1),(.70,1),(1,.53),(.91,-.40),(.68,-1),(-.74,-1),(-1,-.50))
        vertices = []
        for dt, dz in corners:
            x, y, _ = surface(t + dt * half_length, side)
            vertices.append((x + side * .063, y, z + dz * half_height))
        center = surface(t, side)
        anchors[slot] = (center[0] + side * .065, center[1], z)
        face(vertices, metal if slot != 5 else accent, slot)
        for p, q in zip(vertices, vertices[1:] + vertices[:1]):
            face((p, q, (q[0]-side*.04, q[1], q[2]), (p[0]-side*.04, p[1], p[2])), trim, slot)

    if kind not in ("generic", "trackball", "g600", "g300", "cluster"):
        side_pad(3, .53, .073, .245 if angular else .202, .052)
        side_pad(4, .372, .074, .249 if angular else .196, .053)
    if angular:
        side_pad(5, .238, .057, .150, .063)
        anchors[6], anchors[7] = surface(.319, -.85), surface(.185, -.85)
    if kind == "pro":
        side_pad(5, .53, .073, .20, .052, 1)
        side_pad(6, .372, .074, .20, .053, 1)
        anchors[7] = (.18, -.34, -.012)
        face([(.18 + .085 * math.cos(a*math.tau/16), -.34 + .09 * math.sin(a*math.tau/16), -.012)
              for a in range(16)], metal, 7)
    if kind == "g600":
        anchors[5] = surface(.23, .81)
        for i in range(12):
            side_pad(i + 8, .255 + (i // 3) * .085, .036, .30 - (i % 3) * .092, .039)
    if kind == "cluster":
        # These are physical side controls but their wire slots differ across the family.
        for i in range(6):
            side_pad(-1, .29 + (i // 2) * .115, .048, .27 - (i % 2) * .105, .045)
        anchors.pop(-1)
    if kind == "dual":
        for t in (.375, .53):
            side_pad(-1, t, .07, .20, .051, 1)
        anchors.pop(-1)

    # Click wheel and true lateral faces for the G502's two tilt controls.
    wheel_t = .239 if angular else .255
    _, wy, wz = surface(wheel_t, 0)
    radius, width = .152, .105
    wheel = [(wy + .195 * math.cos(i * math.tau / 24), wz - .025 + radius * math.sin(i * math.tau / 24))
             for i in range(24)]
    for i, ((y1, z1), (y2, z2)) in enumerate(zip(wheel, wheel[1:] + wheel[:1])):
        shade = (metal if angular or kind == "master" else (.25,.28,.31)) if i % 2 else (.15, .19, .22)
        face(((-width, y1, z1), (width, y1, z1), (width, y2, z2), (-width, y2, z2)), shade, 2)
    for side, slot in ((1, 9), (-1, 10)):
        disk = [(width * side, yy, zz) for yy, zz in wheel]
        tilt = (4 if side > 0 else 3) if kind == "g600" else slot
        face(disk, (.24,.31,.37), tilt if angular or kind == "g600" else 2)
        if angular or kind == "g600":
            anchors[tilt] = (width * side, wy, wz + radius * .53)
    anchors[2] = (0, wy - .014, wz + radius - .03)

    def top_pad(t, depth, width, color, button, center=0):
        verts = []
        for dt, u in ((-1,-.5), (-.6,-1), (.68,-1), (1,-.55), (1,.55), (.68,1), (-.6,1), (-1,.5)):
            x, y, z = surface(t + dt * depth, center + u * width)
            verts.append((x, y, z + .032))
        face(verts[::-1], color, button)
        if button >= 0:
            x, y, z = surface(t, center)
            anchors[button] = (x, y, z + .033)

    if angular:
        top_pad(.398, .035, .13, trim, -1)  # mechanically switches wheel mode
        top_pad(.495, .041, .17, metal, 8)
    elif kind in ("g305", "g203", "ergo6", "wired6", "diamond6"):
        top_pad(.448, .043, .15, metal, 5)
    elif kind == "g600":
        top_pad(.418, .031, .16, metal, 6)
        top_pad(.502, .031, .16, metal, 7)
    elif kind == "master":
        top_pad(.445, .033, .14, metal, -1)
    elif kind == "g300":
        for slot, center, t in ((3,-.75,.32), (4,-.75,.20), (5,.75,.32), (6,.75,.20),
                                (7,0,.55), (8,0,.45)):
            top_pad(t, .04, .115, metal, slot, center)

    # Add tangible model details without assigning unsupported sensor feature slots.
    if kind in ("g502", "g203", "g600", "g300", "wired6", "diamond6"):
        for segment in range(5):
            y0 = -length/2 - segment*.067
            y1 = y0 - .067
            r = .068 - segment*.007
            for side in range(10):
                a, b = side*math.tau/10, (side+1)*math.tau/10
                face(((r*math.cos(a),y0,.15+r*math.sin(a)),(r*math.cos(b),y0,.15+r*math.sin(b)),
                      (r*math.cos(b),y1,.15+r*math.sin(b)),(r*math.cos(a),y1,.15+r*math.sin(a))), trim)
    if kind == "g305" or angular:
        for t in (.62,) if kind == "g305" else (.66,.69,.72):
            across = [-.8 + i*.10 for i in range(17)] if kind == "g305" else [.16+i*.09 for i in range(6)]
            def stripe(t, u):
                x,y,z = surface(t + .075*(1-abs(u)), u)
                return (x,y,z+.011)
            for u,v in zip(across,across[1:]):
                face((stripe(t,u),stripe(t,v),stripe(t+.007,v),stripe(t+.007,u)), trim if kind == "g305" else metal)
    if kind == "trackball":
        def ball(latitude, longitude):
            return (-.48 + .30*math.cos(latitude)*math.cos(longitude),
                    -.13 + .30*math.cos(latitude)*math.sin(longitude),
                    .51 + .30*math.sin(latitude))
        for row in range(10):
            a, b = (row/10 - .5)*math.pi, ((row+1)/10 - .5)*math.pi
            for column in range(20):
                c, d = column*math.tau/20, (column+1)*math.tau/20
                face((ball(a,c),ball(a,d),ball(b,d),ball(b,c)), (.35,.57,.66))
    if kind == "master":
        for i in range(12):
            y = -.46 + i*.022
            face(((-.66,y,.28),(-.65,y,.41),(-.65,y+.021,.41),(-.66,y+.021,.28)),
                 metal if i%2 else trim)

    def shape(vertex):
        x,y,z = vertex
        if kind == "vertical":
            return (x*.61 + z*.38, y, z*.68 + max(0, z)*(.38 - x)*1.65)
        if kind in ("trackball", "master"):
            return (x*1.12, y, z*(1.12 - .32*x))
        if kind == "g600":
            return (x*1.26, y, z*1.16)
        if kind in ("pro", "superlight"):
            return (x*(.96 + .05*abs(y)), y, z*1.03)
        if kind in ("diamond5", "diamond6"):
            return (x*(1.21 - .43*abs(y)), y, z*.94)
        if kind in ("g300", "dual"):
            return (x*(.88 + .22*abs(y)), y, z*.92)
        if kind in ("wired6", "ergo6", "cluster"):
            return (x*(1.11 - .08*y), y, z*(1.15 - .16*x))
        if kind == "compact":
            return (x*1.05, y*.85, z*.94)
        return vertex
    if kind in ("vertical", "trackball", "master", "g600", "pro", "superlight", "diamond5", "diamond6",
                "g300", "dual", "wired6", "ergo6", "cluster", "compact"):
        faces = [Face(tuple(map(shape, f.vertices)), f.color, f.button, f.light) for f in faces]
        anchors = {i: shape(v) for i,v in anchors.items()}
    return faces, anchors


def project_scene(mesh, width, height, yaw, elevation):
    """Project solid faces in back-to-front order and physical anchor centers."""
    faces, anchors = mesh
    cy, sy = math.cos(yaw), math.sin(yaw)
    ce, se = math.cos(elevation), math.sin(elevation)

    def camera(vertex):
        x, y, z = vertex
        across = x * cy + y * sy
        toward = y * cy - x * sy
        depth = toward * ce + (z - .34) * se
        perspective = 5 / (5 - depth)
        return across * perspective, (toward * se - (z - .34) * ce) * perspective, depth

    coordinates = {v: camera(v) for face in faces for v in face.vertices}
    xs = [p[0] for p in coordinates.values()]
    ys = [p[1] for p in coordinates.values()]
    xmid, ymid = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = min(max(1, width - 110) / (max(xs) - min(xs)), max(1, height - 42) / (max(ys) - min(ys)))

    def screen(p):
        return (width / 2 + scale * (p[0] - xmid), height / 2 + scale * (p[1] - ymid))

    pixels = {v: screen(p) for v, p in coordinates.items()}
    def depth(face):
        return sum(coordinates[v][2] for v in face.vertices) / len(face.vertices)
    polygons = [(face, [pixels[v] for v in face.vertices]) for face in sorted(faces, key=depth)]
    return polygons, {idx: screen(camera(point)) for idx, point in anchors.items()}


def contains(points, x, y):
    inside = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def label_positions(anchors, width, height):
    """Spread targets along the margins without changing their vertical order."""
    positions = {}
    ordered = sorted(anchors, key=lambda i: anchors[i][0])
    half = (len(ordered)+1)//2
    for right, indices in ((False, ordered[:half]), (True, ordered[half:])):
        group = sorted((anchors[idx][1], idx) for idx in indices)
        count = len(group)
        spacing = min(24, (height - 22) / max(1, count - 1))
        previous = 12 - spacing
        for i, (y, idx) in enumerate(group):
            y = max(previous + spacing, min(y, height - 12 - spacing * (count - i - 1)))
            positions[idx] = (width - 23 if right else 23, y)
            previous = y
    return positions


class MouseView(Gtk.DrawingArea):
    """Select a button by click or keyboard; drag the case to orbit around it."""

    def __init__(self, model=None, on_select=None):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.on_select = on_select
        self.set_content_height(278)
        self.set_content_width(220)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)
        self.set_draw_func(self._draw)
        self.pressed = 0
        self.selected = None
        self.hovered = None
        self.polygons = []
        self.labels = {}
        self.scene_key = None
        self.yaw, self.elevation = _HOME_YAW, _HOME_ELEVATION
        self.set_model(model)

        self.drag = Gtk.GestureDrag.new()
        self.drag.set_button(Gdk.BUTTON_PRIMARY)
        self.drag.connect("drag-begin", self._drag_begin)
        self.drag.connect("drag-update", self._drag_update)
        self.drag.connect("drag-end", self._drag_end)
        self.add_controller(self.drag)
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._click_start)
        click.connect("released", self._click_end)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._move)
        motion.connect("leave", lambda *_: self._hover(None))
        self.add_controller(motion)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def set_model(self, model):
        self.kind = model_key(model)
        self.mesh = make_mesh(model)
        self.selected = self.hovered = None
        self.pressed = 0
        self.scene_key = None
        self.yaw, self.elevation = _HOME_YAW, _HOME_ELEVATION
        self._accessibility()
        self.queue_draw()

    def set_pressed(self, mask):
        mask = int(mask or 0)
        if mask != self.pressed:
            self.pressed = mask
            self._accessibility()
            self.queue_draw()

    def set_selected(self, index):
        self.selected = index if index in self.mesh[1] else None
        self._accessibility()
        self.queue_draw()

    def button_name(self, index):
        if index not in self.mesh[1]:
            return f"Button {index + 1}" if type(index) is int and index >= 0 else ""
        if self.kind in ("g502", "g502wireless") and index in (9, 10):
            return _NAMES[self.kind][index]
        return f"{self._short_name(index)} · {_NAMES[self.kind][index]}"

    def _short_name(self, index):
        if self.kind in ("g502", "g502wireless") and index in (9, 10):
            return "Tilt R" if index == 9 else "Tilt L"
        if self.kind in ("generic", "trackball", "vertical", "master", "compact", "cluster"):
            return str(index + 1)
        return f"G{index + 1}"

    def _accessibility(self):
        label = "Mouse button diagram" if self.kind != "generic" else "Representative mouse diagram"
        down = [self.button_name(i) for i in self.mesh[1] if self.pressed & (1 << i)]
        description = "Drag to rotate. Use left and right to select a button, then press Enter."
        if self.selected is not None:
            description += " Selected: " + self.button_name(self.selected) + "."
        if down:
            description += " Pressed: " + ", ".join(down) + "."
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [label, description])

    def _tooltip(self, widget, x, y, keyboard, tooltip):
        slot = self.selected if keyboard else self._hit(x, y)
        text = self.button_name(slot) if slot is not None else "Drag to rotate · Home to reset view"
        tooltip.set_text(text)
        return True

    def _activate(self, slot):
        if slot is not None:
            self.set_selected(slot)
            if self.on_select is not None:
                self.on_select(slot)

    def _hover(self, index):
        if index != self.hovered:
            self.hovered = index
            self.set_cursor_from_name("pointer" if index is not None else "grab")
            self.queue_draw()

    def _move(self, controller, x, y):
        if not self.drag.is_active():
            self._hover(self._hit(x, y))

    def _click_start(self, gesture, count, x, y):
        self.start_xy = (x, y)
        self.grab_focus()

    def _click_end(self, gesture, count, x, y):
        if max(abs(x - self.start_xy[0]), abs(y - self.start_xy[1])) <= 5:
            self._activate(self._hit(x, y))

    def _drag_begin(self, gesture, x, y):
        self.grab_focus()
        self.start_xy = (x, y)
        self.start_angles = (self.yaw, self.elevation)
        self.did_drag = False

    def _drag_update(self, gesture, dx, dy):
        if self.did_drag or max(abs(dx), abs(dy)) > 5:
            self.did_drag = True
            self.hovered = None
            self.yaw = self.start_angles[0] + dx * .012
            self.elevation = max(-1.48, min(1.51, self.start_angles[1] - dy * .010))
            self.set_cursor_from_name("grabbing")
            self.queue_draw()

    def _drag_end(self, gesture, dx, dy):
        self.set_cursor_from_name("grab")

    def _key(self, controller, key, code, modifiers):
        if key in (Gdk.KEY_Left, Gdk.KEY_Right):
            direction = -1 if key == Gdk.KEY_Left else 1
            count = len(self.mesh[1])
            index = (self.selected + direction) % count if self.selected is not None else 0
            self.set_selected(index)
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._activate(self.selected if self.selected is not None else 0)
        elif key in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.elevation = max(-1.48, min(1.51, self.elevation + (.15 if key == Gdk.KEY_Up else -.15)))
            self.queue_draw()
        elif key == Gdk.KEY_Home:
            self.yaw, self.elevation = _HOME_YAW, _HOME_ELEVATION
            self.queue_draw()
        else:
            return False
        return True

    def _hit(self, x, y):
        for slot, (lx, ly) in self.labels.items():
            if abs(x - lx) <= 19 and abs(y - ly) <= 11:
                return slot
        for face, points in reversed(self.polygons):
            if contains(points, x, y):
                return face.button if face.button >= 0 else None
        return None

    def _draw(self, widget, ctx, width, height):
        if width < 45 or height < 32:
            return
        key = (width, height, self.yaw, self.elevation)
        if self.scene_key != key:
            self.polygons, self.anchors = project_scene(self.mesh, *key)
            self.labels = label_positions(self.anchors, width, height)
            self.scene_key = key
        ink = self.get_style_context().get_color()
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.save()
        ctx.translate(width / 2, height * .77)
        ctx.scale(max(1, width * .26), max(1, height * .16))
        glow = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
        glow.add_color_stop_rgba(0, .08,.11,.13,.22)
        glow.add_color_stop_rgba(1, .08,.11,.13,0)
        ctx.set_source(glow)
        ctx.arc(0, 0, 1, 0, math.tau)
        ctx.fill()
        ctx.restore()
        for face, points in self.polygons:
            slot = face.button
            color = face.color
            if slot >= 0:
                if self.pressed & (1 << slot):
                    color = (.21,.91,.63)
                elif slot == self.selected:
                    color = (.24,.66,.78)
                elif slot == self.hovered:
                    color = (.40,.60,.70)
            ctx.set_source_rgb(*(min(1, x * face.light) for x in color))
            ctx.move_to(*points[0])
            for p in points[1:]:
                ctx.line_to(*p)
            ctx.close_path()
            ctx.fill_preserve()
            ctx.set_line_width(.65)
            ctx.stroke()

        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(10)
        for slot, (lx, ly) in self.labels.items():
            px, py = self.anchors[slot]
            active = bool(self.pressed & (1 << slot))
            emph = active or slot == self.selected or slot == self.hovered
            left = lx < width / 2
            lead = lx + (15 if left else -15)
            ctx.set_source_rgba(ink.red, ink.green, ink.blue, .65 if emph else .32)
            ctx.set_line_width(1.4 if emph else .8)
            ctx.move_to(lead, ly)
            ctx.line_to(lead + (6 if left else -6), ly)
            ctx.line_to(px, py)
            ctx.stroke()
            if active:
                ctx.set_source_rgb(.22, .90, .64)
            else:
                ctx.set_source_rgb(.54, .84, .88)
            ctx.arc(px, py, 2.4 if emph else 1.7, 0, math.tau)
            ctx.fill()
            if emph:
                ctx.set_source_rgb(*((.12, .36, .38) if active else (.15, .33, .42)))
            else:
                ctx.set_source_rgba(ink.red, ink.green, ink.blue, .12)
            for cx, cy, a in ((lx+10,ly-5,-math.pi/2),(lx+10,ly+5,0),
                              (lx-10,ly+5,math.pi/2),(lx-10,ly-5,math.pi)):
                ctx.arc(cx, cy, 4, a, a + math.pi/2)
            ctx.close_path()
            ctx.fill()
            text = self._short_name(slot)
            ext = ctx.text_extents(text)
            if emph:
                ctx.set_source_rgb(1,1,1)
            else:
                ctx.set_source_rgb(ink.red, ink.green, ink.blue)
            ctx.move_to(lx - ext.width/2 - ext.x_bearing, ly - ext.height/2 - ext.y_bearing)
            ctx.show_text(text)
