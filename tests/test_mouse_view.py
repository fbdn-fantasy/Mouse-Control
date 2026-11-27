import unittest
from types import SimpleNamespace

from mouse_control.mouse_view import MouseView, contains, label_positions, make_mesh, model_key, project_scene


class MouseViewTests(unittest.TestCase):
    def test_physical_controls_have_a_selectable_face_and_label(self):
        models = {"G305": 6, "G502 HERO": 11, "G502 LIGHTSPEED": 11, "G203 LIGHTSYNC": 6,
                  "G PRO Wireless": 8, "G PRO X SUPERLIGHT": 5, "G600": 20, "MX Master 3S": 5,
                  "MX Vertical": 5, "MX Ergo": 3, "Unrecognized mouse": 3, "G300s": 9,
                  "G604": 3, "G403 HERO": 6, "G703": 6, "G303 Shroud Edition": 5,
                  "G303 Daedalus Apex": 6, "MX Anywhere 3S": 5, "G903": 5}
        for name, count in models.items():
            with self.subTest(model=name):
                mesh = make_mesh(name)
                faces, anchors = mesh
                self.assertEqual(set(range(count)), set(anchors))
                self.assertEqual(set(anchors), {face.button for face in faces if face.button >= 0})
                polygons, screen = project_scene(mesh, 400, 250, .78, 1)
                labels = label_positions(screen, 400, 250)
                state = SimpleNamespace(polygons=polygons, labels=labels)
                for button, (x, y) in labels.items():
                    self.assertGreaterEqual(y, 11)
                    self.assertLessEqual(y, 239)
                    self.assertEqual(MouseView._hit(state, x, y), button)
                self.assertIsNone(MouseView._hit(state, 0, 0))

    def test_rotation_reprojects_anatomical_targets(self):
        mesh = make_mesh("G502 HERO")
        _, front = project_scene(mesh, 460, 300, .65, 1.0)
        _, back = project_scene(mesh, 460, 300, .65 + 3.14159, .1)
        self.assertEqual(set(front), set(back))
        self.assertGreater(abs(front[0][0] - back[0][0]), 50)
        self.assertEqual(model_key(0x4074), model_key("Logitech G305 LIGHTSPEED"))
        self.assertEqual(model_key(0xC08D), model_key("G502 LIGHTSPEED"))
        self.assertNotEqual(make_mesh("G502 HERO")[0], make_mesh("G502 LIGHTSPEED")[0])

    def test_touch_chooses_frontmost_part_and_ignores_obstructed_buttons(self):
        rect = [(0, 0), (40, 0), (40, 40), (0, 40)]
        overlay = [(10, 10), (30, 10), (30, 30), (10, 30)]
        state = SimpleNamespace(labels={}, polygons=[(SimpleNamespace(button=1), rect),
                                                      (SimpleNamespace(button=-1), overlay)])
        self.assertTrue(contains(rect, 2, 2))
        self.assertEqual(MouseView._hit(state, 2, 2), 1)
        self.assertIsNone(MouseView._hit(state, 20, 20))
