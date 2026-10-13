#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_host_safe_attach_static.py: the safety-relevant shape of the verify-first host attach
# (bin/pi/lekiwi_host_safe_attach.py) and the absence of any identity in the files promoted with it.
# WHY STATIC: the wrapper imports lerobot and patches LeKiwi at import time, and what it protects is a holding arm. It is
# therefore never imported here: it is PARSED (ast). The two launchers are RUN, but only with stub ssh/scp/python3/pgrep
# first on PATH, so that not even a regression in them could reach a network or a robot: into their refusals with stubs
# that fail, and (start-host.sh only) to the end with stubs that answer, to see what WOULD have been sent.
# Every identity-shaped string this file needs is BUILT AT RUNTIME from parts: this file is committed and scanned too.
# Run directly: python3 autonomy/tests/test_host_safe_attach_static.py
import ast
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WRAPPER = os.path.join(ROOT, "bin", "pi", "lekiwi_host_safe_attach.py")
START_HOST = os.path.join(ROOT, "bin", "pi", "start-host.sh")
RUN_CYCLE = os.path.join(ROOT, "bin", "laptop", "run-cycle.sh")
SETUP_DOC = os.path.join(ROOT, "docs", "autonomous-demo", "robot-host-setup.md")
BOUNDARY = os.path.join(ROOT, "bin", "pi", "host_boundary.py")
BOUNDARY_DOC = os.path.join(ROOT, "docs", "autonomous-demo", "host-boundary.md")
PROMOTED = (WRAPPER, START_HOST, RUN_CYCLE, SETUP_DOC, BOUNDARY, BOUNDARY_DOC, os.path.abspath(__file__))
LICENSE_LINE = "Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE."

# The executable part of the wrapper (from its first import to the end of the file) is, byte for byte, the code that was
# demonstrated on the robot. This pin makes ANY change to it a deliberate act: whoever changes what holds the arm
# re-reads the invariants below, re-proves the change on hardware, and then updates the digest in the same commit.
DEMONSTRATED_BODY_SHA256 = "917b461a8491209d4932405d1a6e17c823adc64bcf57d7f2024d86179cde3640"
# ONE change is AWAITING that re-proof (2026-10-01, under the operator ruling of 2026-09-27: "Fix the trip. This shouldn't
# have happened and should never fail in the same way in other places."). What changed in the body: the constant
# WHEEL_REST_MAX_RAW (50) with its comment; the wheel check and its problem text, which accept a present velocity of at
# most one count (|v| <= 50 raw, either sign) where exactly 0 was required, the goal velocity still exactly 0; the OK line,
# which prints the velocities it read where it printed a literal 0; and the zero-speed hold's comment. Every other line of
# the body is the demonstrated one. The change is reviewed and proven on the fake bus (test_host_boundary.py,
# TheAttachTakesOneCountOfWheelDitherAsRest); it is NOT demonstrated on the robot. While this is set, the body must be
# exactly this digest. The commit that records a PASS of the attended re-proof (this body installed with
# bin/pi/lekiwi-deploy.sh install, then armed) moves it into DEMONSTRATED_BODY_SHA256 and sets this to None, with the
# evidence; a FAIL reverts the change instead.
AWAITING_REPROOF_BODY_SHA256 = "53d8356714f61246380ea117014668fab08cbe4eeb97a2ed8a7c108f34802d2d"
# The ONE addition since: the delimited host boundary block (bin/pi/host_boundary.py, OFF unless KC_HOST_BOUNDARY=1). It is
# cut out before the digest above is taken, so that digest still says "everything else is what was demonstrated", and it
# carries a pin of its own. The block has NOT been demonstrated on the robot: its pin records what was reviewed, no more.
BOUNDARY_BLOCK = re.compile(r"(?ms)^# --- HOST BOUNDARY begin[^\n]*\n.*?^# --- HOST BOUNDARY end ---\n\n")
BOUNDARY_BLOCK_SHA256 = "cc5c23c28b0773cf0a4ed910e45cd1de861437be41b3bbb43b52680040daf9fb"
# The SECOND addition: the delimited host command loopback block (OFF unless KC_HOST_COMMAND_LOOPBACK=1, which only the
# Cake-supervised run-host.sh sets). It is cut out the same way and pinned the same way. Its mechanism was run on the
# robot against lerobot's own LeKiwiHost with the robot's pyzmq on spare ports, which opens no device; the host has not
# yet attached with it, so this pin, too, records what was reviewed.
LOOPBACK_BLOCK = re.compile(r"(?ms)^# --- HOST COMMAND LOOPBACK begin[^\n]*\n.*?^# --- HOST COMMAND LOOPBACK end ---\n\n")
LOOPBACK_BLOCK_SHA256 = "8cc96b5d64a410cdd1cf79441aaf0f2e207274e35814af174481b22610764cbe"
MOTOR_ACCESS = {"send_action", "stop_base", "sync_write", "sync_read", "connect", "disconnect"}  # nothing the boundary may call on a robot or a bus

ARM_SAFE_BUS_METHODS = {"sync_read", "read", "write"}  # everything configure may call on the bus; write is constrained below
WRITE_LIKE = re.compile(r"write|torque|calib|configure|homing|reset|^set_|^enable|^disable")  # names that could change a servo
BASE_WRITES = [("Goal_Velocity", 0), ("Torque_Enable", 1)]  # the zero-speed wheel hold, in this order, and nothing else


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def called_attr(node):
    """'x.y(...)' -> 'y', anything else -> None."""
    return node.func.attr if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) else None


def is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def calls(tree, attr=None, name=None):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ((attr is not None and called_attr(node) == attr) or (name is not None and is_name(node.func, name))):
            yield node


def constants(tree):
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]


class WrapperShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read_text(WRAPPER)
        cls.tree = ast.parse(cls.source, WRAPPER)
        cls.parents = {child: parent for parent in ast.walk(cls.tree) for child in ast.iter_child_nodes(parent)}
        cls.functions = {node.name: node for node in ast.walk(cls.tree) if isinstance(node, ast.FunctionDef)}
        # 'LeKiwi.<method> = <function name>' statements, wherever they are.
        cls.patches = {}
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute) and is_name(node.targets[0].value, "LeKiwi"):
                cls.patches.setdefault(node.targets[0].attr, []).append(node)

    def ancestors(self, node):
        while node in self.parents:
            node = self.parents[node]
            yield node

    def patched(self, method):
        self.assertEqual(len(self.patches.get(method, [])), 1, f"LeKiwi.{method} must be replaced exactly once")
        node = self.patches[method][0]
        self.assertIsInstance(node.value, ast.Name)
        return node, self.functions[node.value.id]

    def main_guard(self):
        guards = [n for n in self.tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and is_name(n.test.left, "__name__")]
        self.assertEqual(len(guards), 1)
        return guards[0]

    # --- it is never imported, so it must at least be what was demonstrated -------------------------------------------
    def test_this_test_never_imports_lerobot_or_the_wrapper(self):
        imported = set()
        for node in ast.walk(ast.parse(read_text(os.path.abspath(__file__)))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertFalse(imported & {"lerobot", "lekiwi_host_safe_attach", "zmq", "cv2"}, imported)

    def test_executable_body_is_the_demonstrated_one_or_the_one_awaiting_its_reproof(self):
        text = self.source.replace("\r\n", "\n")
        body = text[text.index("\nimport os\n") + 1:]
        self.assertEqual(len(BOUNDARY_BLOCK.findall(body)), 1, "the host boundary block must be there exactly once, between its two marker lines")
        self.assertEqual(len(LOOPBACK_BLOCK.findall(body)), 1, "the host command loopback block must be there exactly once, between its two marker lines")
        pinned = AWAITING_REPROOF_BODY_SHA256 or DEMONSTRATED_BODY_SHA256  # exactly one body is accepted at any time
        self.assertEqual(hashlib.sha256(LOOPBACK_BLOCK.sub("", BOUNDARY_BLOCK.sub("", body)).encode("utf-8")).hexdigest(), pinned,
                         "the executable part of the wrapper changed: re-check every invariant in this file, re-prove it on hardware, then update the pin")
        self.assertEqual(hashlib.sha256(BOUNDARY_BLOCK.search(body).group(0).encode("utf-8")).hexdigest(), BOUNDARY_BLOCK_SHA256,
                         "the host boundary block changed: re-check the four tests below, then update its pin")
        self.assertEqual(hashlib.sha256(LOOPBACK_BLOCK.search(body).group(0).encode("utf-8")).hexdigest(), LOOPBACK_BLOCK_SHA256,
                         "the host command loopback block changed: re-check its tests below, then update its pin")

    def test_a_body_awaiting_its_reproof_is_never_recorded_as_the_demonstrated_one(self):
        if AWAITING_REPROOF_BODY_SHA256 is not None:
            self.assertRegex(AWAITING_REPROOF_BODY_SHA256, r"^[0-9a-f]{64}$")
            self.assertNotEqual(AWAITING_REPROOF_BODY_SHA256, DEMONSTRATED_BODY_SHA256, "a re-proven body is DEMONSTRATED, and this is then None")

    # --- the host command loopback: opt-in, one wrapped constructor, loopback only, read back, no motor access ---------
    def loopback_block(self):
        gates = [n for n in self.tree.body if isinstance(n, ast.If) and "KC_HOST_COMMAND_LOOPBACK" in constants(n.test)]
        self.assertEqual(len(gates), 1, "exactly one statement depends on KC_HOST_COMMAND_LOOPBACK, at module level")
        return gates[0]

    def test_command_loopback_is_opt_in(self):
        gate = self.loopback_block()
        self.assertEqual(ast.dump(gate.test), ast.dump(ast.parse('os.environ.get("KC_HOST_COMMAND_LOOPBACK") == "1"', mode="eval").body), "only the exact value 1 turns it on")
        self.assertFalse(gate.orelse)
        self.assertEqual([n for n in ast.walk(self.tree) if isinstance(n, ast.Constant) and n.value == "KC_HOST_COMMAND_LOOPBACK" and gate not in list(self.ancestors(n))], [])
        for name in ("zmq", "_CommandLoopbackHost", "_host_init_command_on_loopback", "_stock_host_init"):
            uses = [n for n in ast.walk(self.tree) if isinstance(n, ast.Name) and n.id == name]
            self.assertTrue(all(gate in list(self.ancestors(n)) for n in uses), f"{name} is used outside the opt-in block")
        order = self.tree.body.index(gate)
        self.assertGreater(order, self.tree.body.index(self.boundary_block()), "it wraps the constructor after everything else the wrapper replaces")
        self.assertLess(order, self.tree.body.index(self.main_guard()), "it is in place before main() can construct the host")

    def test_command_loopback_wraps_only_the_host_constructor(self):
        gate = self.loopback_block()
        imports = [(type(s).__name__, getattr(s, "module", None), [(a.name, a.asname) for a in s.names]) for s in gate.body if isinstance(s, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [("Import", None, [("time", None)]), ("Import", None, [("zmq", None)]),
                                   ("ImportFrom", "lerobot.robots.lekiwi.lekiwi_host", [("LeKiwiHost", "_CommandLoopbackHost")])])
        assigns = [n for n in ast.walk(gate) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Attribute)]
        self.assertEqual(sorted((ast.unparse(n.targets[0]), ast.unparse(n.value)) for n in assigns),
                         [("_CommandLoopbackHost.__init__", "_host_init_command_on_loopback"), ("self.zmq_cmd_socket", "replacement")])
        called = {called_attr(n) for n in ast.walk(gate) if isinstance(n, ast.Call)}
        self.assertFalse(called & MOTOR_ACCESS, called & MOTOR_ACCESS)
        self.assertFalse({"send_action", "get_observation", "stop_base"} & {n.attr for n in ast.walk(gate) if isinstance(n, ast.Attribute)})

    def test_command_loopback_binds_loopback_and_reads_the_endpoint_back(self):
        init = self.functions["_host_init_command_on_loopback"]
        self.assertEqual(ast.unparse(init.body[1]), "_stock_host_init(self, config)", "the stock constructor runs first, unchanged")
        binds = [n for n in ast.walk(init) if called_attr(n) == "bind"]
        self.assertEqual([ast.unparse(n.args[0]) for n in binds], ["wanted"])
        wanted = [n for n in ast.walk(init) if isinstance(n, ast.Assign) and is_name(n.targets[0], "wanted")]
        self.assertEqual(len(wanted), 1)
        self.assertIsInstance(wanted[0].value, ast.JoinedStr)
        self.assertEqual(wanted[0].value.values[0].value, "tcp://127.0.0.1:", "the replacement binds the loopback address and nothing else")
        self.assertEqual({ast.unparse(n) for n in calls(init, attr="setsockopt")}, {"replacement.setsockopt(zmq.CONFLATE, 1)"}, "the stock socket kind and option")
        self.assertIn("replacement = self.zmq_context.socket(zmq.PULL)", ast.unparse(init))
        source = ast.unparse(init)
        self.assertIn("bound = replacement.getsockopt_string(zmq.LAST_ENDPOINT)", source)
        self.assertIn("if bound != wanted:", source)
        self.assertLess(source.index("if bound != wanted:"), source.index("self.zmq_cmd_socket = replacement"), "the endpoint is checked before the host may use the socket")
        raised = [ast.unparse(n.exc.func) for n in ast.walk(init) if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)]
        self.assertEqual(raised, ["SystemExit"] * 3, "every refusal ends the process before the host loop")

    # --- the host boundary: opt-in, after everything the wrapper replaces, and without any motor access ----------------
    def boundary_block(self):
        gates = [n for n in self.tree.body if isinstance(n, ast.If) and "KC_HOST_BOUNDARY" in constants(n.test)]
        self.assertEqual(len(gates), 1, "exactly one statement depends on KC_HOST_BOUNDARY, at module level")
        return gates[0]

    def test_host_boundary_is_opt_in_and_unset_means_nothing_is_imported(self):
        gate = self.boundary_block()
        self.assertEqual(ast.dump(gate.test), ast.dump(ast.parse('os.environ.get("KC_HOST_BOUNDARY") == "1"', mode="eval").body), "only the exact value 1 turns it on")
        self.assertFalse(gate.orelse)
        mentions = [n for n in ast.walk(self.tree) if (isinstance(n, ast.Name) and n.id == "host_boundary") or (isinstance(n, ast.Import) and any(a.name == "host_boundary" for a in n.names))
                    or (isinstance(n, ast.ImportFrom) and "host_boundary" in (n.module or ""))]
        self.assertTrue(mentions)
        self.assertTrue(all(gate in list(self.ancestors(n)) for n in mentions), "host_boundary is imported or used outside the opt-in block: start-host.sh copies that module only with the word 'boundary', and only KC_HOST_BOUNDARY=1 may make the host import it")
        self.assertEqual([n for n in ast.walk(self.tree) if isinstance(n, ast.Constant) and n.value == "KC_HOST_BOUNDARY" and gate not in list(self.ancestors(n))], [])

    def test_host_boundary_block_only_installs(self):
        gate = self.boundary_block()
        kinds = [type(s) for s in gate.body]
        self.assertEqual(kinds, [ast.Import, ast.ImportFrom, ast.Expr])
        self.assertEqual((gate.body[1].module, [a.name for a in gate.body[1].names]), ("lerobot.robots.lekiwi.lekiwi_host", ["LeKiwiHost"]))
        install = gate.body[2].value
        self.assertEqual(ast.dump(install), ast.dump(ast.parse("host_boundary.install(LeKiwi, LeKiwiHost)", mode="eval").body))
        self.assertFalse([n for n in ast.walk(gate) if isinstance(n, ast.Attribute) and n.attr == "bus"])

    def test_host_boundary_is_installed_after_every_replacement_and_before_main(self):
        gate = self.boundary_block()
        for method in ("configure", "calibrate"):
            self.assertLess(self.patched(method)[0].lineno, gate.lineno, f"the boundary must not come before LeKiwi.{method} is replaced")
        self.assertLess(self.functions["_verify_only_configure"].lineno, gate.lineno)
        self.assertLess(gate.lineno, self.main_guard().lineno)
        self.assertNotIn(gate, list(ast.walk(self.main_guard())))

    def test_host_boundary_module_has_no_motor_access(self):
        tree = ast.parse(read_text(BOUNDARY), BOUNDARY)
        imported = {alias.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names} | {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse(imported & {"lerobot", "serial", "scservo_sdk", "cv2", "threading", "subprocess", "socket"}, imported)
        self.assertFalse([n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("bus", "arm_motors", "base_motors")], "the boundary reaches for the servo bus")
        for call in ast.walk(tree):
            attr = called_attr(call)
            if attr is not None:
                self.assertFalse(WRITE_LIKE.search(attr) or attr in MOTOR_ACCESS, f"host_boundary.py line {call.lineno}: .{attr}() could reach a motor")
        # The only robot method it ever names is the one it wraps, and it takes it from the class it is given.
        named = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and is_name(n.value, "robot_cls")}
        self.assertEqual(named, {"get_observation"})

    # --- refusals at start ------------------------------------------------------------------------------------------
    def test_refuses_to_start_without_the_two_safety_flags(self):
        body = self.main_guard().body
        main_at = [i for i, s in enumerate(body) if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and is_name(s.value.func, "main")]
        self.assertEqual(len(main_at), 1, "main() is called exactly once, inside the __main__ guard")
        self.assertEqual(len(list(calls(self.tree, name="main"))), 1, "main() is called nowhere else")
        refusals = {}
        for statement in body[:main_at[0]]:  # only what runs BEFORE the stock main() counts
            if not isinstance(statement, ast.If):
                continue
            exits = [c for c in calls(statement, attr="exit") if is_name(c.func.value, "sys")]
            negated = isinstance(statement.test, ast.UnaryOp) and isinstance(statement.test.op, ast.Not)
            absent = isinstance(statement.test, ast.Compare) and isinstance(statement.test.ops[0], ast.NotIn)
            if exits and (negated or absent) and not statement.orelse:
                for value in constants(statement.test):
                    refusals[value] = statement
        self.assertIn("--robot.cameras={}", refusals, "must refuse when the host could own a camera")
        self.assertIn("--robot.disable_torque_on_disconnect=false", refusals, "must refuse when stopping the host could drop the arm")

    def test_parsed_configuration_is_rechecked_before_any_bus_access(self):
        # The argv refusals compare STRINGS; what every exit path depends on is the PARSED configuration, so it is checked again.
        _, configure = self.patched("configure")
        guard = [n for n in configure.body if isinstance(n, ast.If) and {"disable_torque_on_disconnect", "cameras"} <= {a.attr for a in ast.walk(n.test) if isinstance(a, ast.Attribute)}]
        self.assertEqual(len(guard), 1)
        self.assertTrue(list(calls(guard[0], name="_refuse")))
        first_bus_call = min(c.lineno for c in ast.walk(configure) if called_attr(c) in ARM_SAFE_BUS_METHODS and is_name(c.func.value, "bus"))
        self.assertLess(guard[0].lineno, first_bus_call)

    # --- configure: verify first, never write the arm ----------------------------------------------------------------
    def test_configure_calls_nothing_on_the_bus_but_reads_and_the_constrained_write(self):
        _, configure = self.patched("configure")
        for call in ast.walk(configure):
            attr = called_attr(call)
            if attr is None:
                continue
            if is_name(call.func.value, "bus"):
                self.assertIn(attr, ARM_SAFE_BUS_METHODS, f"line {call.lineno}: bus.{attr}() is not a read or the constrained write")
            elif WRITE_LIKE.search(attr):
                self.fail(f"line {call.lineno}: .{attr}() could change a servo and is not a constrained bus.write")
        self.assertFalse([n for n in ast.walk(configure) if isinstance(n, ast.Attribute) and n.attr == "bus" and not is_name(n.value, "self")])

    def test_configure_never_writes_an_arm_motor(self):
        _, configure = self.patched("configure")
        # 'base' is bound once, from self.base_motors, and never rebound: a write addressed through it cannot reach the arm.
        stores = [n for n in ast.walk(configure) if isinstance(n, ast.Name) and n.id == "base" and isinstance(n.ctx, ast.Store)]
        self.assertEqual(len(stores), 1, "'base' must be bound exactly once")
        binding = self.parents[self.parents[stores[0]]]
        self.assertIsInstance(binding, ast.Assign)
        value = binding.value.elts[binding.targets[0].elts.index(stores[0])]
        self.assertEqual(ast.dump(value), ast.dump(ast.parse("list(self.base_motors)", mode="eval").body))
        writes = sorted(calls(configure, attr="write"), key=lambda c: c.lineno)
        self.assertEqual([(w.args[0].value, w.args[2].value) for w in writes], BASE_WRITES, "the only writes are the zero-speed wheel hold")
        for write in writes:
            self.assertTrue(is_name(write.args[1], "m"), f"line {write.lineno}: the written motor must be the loop variable")
            loops = [a for a in self.ancestors(write) if isinstance(a, ast.For)]
            self.assertEqual(len(loops), 1)
            self.assertTrue(is_name(loops[0].target, "m") and is_name(loops[0].iter, "base"), f"line {write.lineno}: writes happen only in 'for m in base'")
            # ... and only for a wheel whose torque was read as not ON: a holding wheel is never written.
            conditions = [a for a in self.ancestors(write) if isinstance(a, ast.If) and loops[0] in list(self.ancestors(a))]
            self.assertTrue(any(isinstance(s, ast.Subscript) and is_name(s.value, "torque") and is_name(s.slice, "m") for c in conditions for s in ast.walk(c.test)))
            self.assertFalse([k for k in write.keywords if k.arg != "normalize"])

    def test_zero_velocity_is_written_and_read_back_before_wheel_torque(self):
        _, configure = self.patched("configure")
        velocity, torque = sorted(calls(configure, attr="write"), key=lambda c: c.lineno)
        readbacks = [n for n in ast.walk(configure) if isinstance(n, ast.If) and velocity.lineno < n.lineno < torque.lineno
                     and any(c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "Goal_Velocity" for c in calls(n.test, attr="read")) and list(calls(n, name="_refuse"))]
        self.assertEqual(len(readbacks), 1, "a wheel is never energised before its zero target read back as 0")

    def test_every_check_and_refusal_precedes_the_first_write(self):
        _, configure = self.patched("configure")
        first_write = min(c.lineno for c in calls(configure, attr="write"))
        self.assertTrue(all(c.lineno < first_write for c in calls(configure, attr="sync_read")))
        verdict = [n for n in configure.body if isinstance(n, ast.If) and is_name(n.test, "problems")]
        self.assertEqual(len(verdict), 1)
        self.assertTrue(list(calls(verdict[0], name="_refuse")))
        self.assertLess(verdict[0].lineno, first_write)
        for register in ("Torque_Enable", "Operating_Mode", "Present_Position", "Goal_Position", "Present_Velocity", "Goal_Velocity"):
            self.assertIn(register, [c.args[0].value for c in calls(configure, attr="sync_read")], f"{register} is no longer verified")
        self.assertIn("Phase", [c.args[0].value for c in calls(configure, attr="read") if isinstance(c.args[0], ast.Constant)])

    def test_never_falls_back_to_the_stock_configure_or_calibrate(self):
        # The stock methods are never saved and never read: there is nothing to fall back TO.
        loads = {n.attr for n in ast.walk(self.tree) if isinstance(n, ast.Attribute) and is_name(n.value, "LeKiwi") and isinstance(n.ctx, ast.Load)}
        self.assertLessEqual(loads, {"send_action"})
        self.assertFalse(list(calls(self.tree, attr="configure")) + list(calls(self.tree, attr="calibrate")))

    # --- the refusal itself -----------------------------------------------------------------------------------------
    def test_refusal_keeps_torque_and_skips_destructors(self):
        refuse = self.functions["_refuse"]
        disconnects = list(calls(self.tree, attr="disconnect"))
        self.assertEqual(len(disconnects), 1)
        self.assertIn(disconnects[0], list(ast.walk(refuse)))
        self.assertEqual([(k.arg, k.value.value) for k in disconnects[0].keywords], [("disable_torque", False)])
        self.assertFalse(disconnects[0].args)
        attempts = [n for n in refuse.body if isinstance(n, ast.Try)]
        self.assertEqual(len(attempts), 1)
        hard_exits = [c for c in calls(ast.Module(body=attempts[0].finalbody, type_ignores=[]), attr="_exit") if is_name(c.func.value, "os")]
        self.assertEqual([c.args[0].value for c in hard_exits], [3], "os._exit in 'finally': no destructor may run and drop torque")
        self.assertFalse([c for c in ast.walk(refuse) if called_attr(c) and WRITE_LIKE.search(called_attr(c)) and called_attr(c) != "disconnect"])

    def test_calibrate_is_replaced_by_a_refusal(self):
        patch, calibrate = self.patched("calibrate")
        self.assertIn(patch, self.tree.body, "the replacement is unconditional, at module level")
        self.assertEqual(len(calibrate.body), 1)
        only = calibrate.body[0]
        self.assertTrue(isinstance(only, ast.Expr) and isinstance(only.value, ast.Call) and is_name(only.value.func, "_refuse"))
        self.assertFalse([n for n in ast.walk(calibrate) if isinstance(n, ast.Attribute) and n.attr == "bus"], "a refusing calibrate touches no bus")

    def test_configure_replacement_is_unconditional(self):
        patch, _ = self.patched("configure")
        self.assertIn(patch, self.tree.body)

    # --- scan mode --------------------------------------------------------------------------------------------------
    def test_scan_mode_is_opt_in_and_strips_the_gripper_key(self):
        flag = [n for n in self.tree.body if isinstance(n, ast.Assign) and is_name(n.targets[0], "SCAN_MODE")]
        self.assertEqual(len(flag), 1)
        self.assertIn("KC_SCAN_MODE", constants(flag[0]))
        self.assertIn("1", constants(flag[0]))
        patch, sender = self.patched("send_action")
        enclosing = [a for a in self.ancestors(patch) if isinstance(a, ast.If)]
        self.assertTrue(len(enclosing) == 1 and is_name(enclosing[0].test, "SCAN_MODE"), "normal mode keeps the stock send_action")
        filters = [n for n in ast.walk(sender) if isinstance(n, ast.DictComp)]
        self.assertEqual(len(filters), 1)
        condition = filters[0].generators[0].ifs[0]
        self.assertIsInstance(condition.ops[0], ast.NotEq)
        self.assertTrue(any(is_name(n, "GRIPPER") for n in ast.walk(condition)) and ".pos" in constants(condition))

    def test_scan_mode_requires_a_limp_gripper_and_excludes_it_from_the_held_joints(self):
        _, configure = self.patched("configure")
        scan = [n for n in configure.body if isinstance(n, ast.If) and is_name(n.test, "SCAN_MODE")]
        self.assertEqual(len(scan), 1)
        self.assertTrue(any(isinstance(s, ast.Subscript) and is_name(s.value, "torque") and is_name(s.slice, "GRIPPER") for s in ast.walk(scan[0])))
        self.assertTrue(any(isinstance(n, ast.Name) and n.id == "arm" and isinstance(n.ctx, ast.Store) for n in ast.walk(scan[0])))

    # --- a wheel at rest: at most one count, goal exactly 0 (operator ruling 2026-09-27) ---------------------------------
    def test_a_wheel_reads_at_rest_at_most_one_count_and_its_goal_velocity_exactly_zero(self):
        # The rule of autonomy/manipulation/base_rest.py in the unit this check reads (raw Present_Velocity, one count = 50,
        # either sign), and the goal velocity, a command register, still exactly 0. One constant, bound once, holds the count.
        stores = [n for n in ast.walk(self.tree) if isinstance(n, ast.Name) and n.id == "WHEEL_REST_MAX_RAW" and isinstance(n.ctx, ast.Store)]
        self.assertEqual(len(stores), 1, "WHEEL_REST_MAX_RAW must be bound exactly once")
        binding = self.parents[stores[0]]
        self.assertIn(binding, self.tree.body, "at module level")
        self.assertEqual(ast.literal_eval(binding.value), 50)
        _, configure = self.patched("configure")
        checks = [n for n in ast.walk(configure) if isinstance(n, ast.If) and any(is_name(x, "velocity") for x in ast.walk(n.test))]
        self.assertEqual(len(checks), 1, "one check reads the present velocity")
        self.assertEqual(ast.dump(checks[0].test),
                         ast.dump(ast.parse("abs(int(velocity[m])) > WHEEL_REST_MAX_RAW or int(goal_velocity[m]) != 0", mode="eval").body))
        loops = [a for a in self.ancestors(checks[0]) if isinstance(a, ast.For)]
        self.assertTrue(len(loops) == 1 and is_name(loops[0].target, "m") and is_name(loops[0].iter, "base"), "the check runs on every wheel")
        self.assertTrue(all(isinstance(s, ast.Expr) and called_attr(s.value) == "append" and is_name(s.value.func.value, "problems") for s in checks[0].body),
                        "a wheel outside the rule is a problem, judged with every other one before the first write")
        doc = " ".join(ast.get_docstring(self.tree).split())
        self.assertIn("at most ONE velocity count", doc)
        self.assertNotIn("present and goal velocity 0", doc)

    # --- the description must not promise more than the code does ------------------------------------------------------
    def test_description_states_the_base_writes_and_carries_no_local_banner(self):
        doc = " ".join(ast.get_docstring(self.tree).split())
        for fact in ("Goal_Velocity 0", "Torque_Enable 1", "NO ARM REGISTER IS WRITTEN", "NOT read-only", "DOES WRITE TO THE BASE"):
            self.assertIn(fact, doc)
        for stale in ("GITIGNORED", "WITHOUT ANY WRITE"):  # the local banner, and the old claim that a passing attach writes nothing
            self.assertNotIn(stale, self.source)


class LauncherShape(unittest.TestCase):
    IDENTITIES = ("KC_PI_SSH", "KC_PI_IP", "KC_ROBOT_ID", "KC_SERVO_PORT", "KC_WRIST_CAMERA_ID", "KC_FRONT_CAMERA_ID")
    REQUIRED = {START_HOST: ("KC_PI_SSH", "KC_ROBOT_ID", "KC_SERVO_PORT"), RUN_CYCLE: ("KC_PI_SSH", "KC_PI_IP", "KC_ROBOT_ID", "KC_WRIST_CAMERA_ID", "KC_FRONT_CAMERA_ID")}

    def test_license_header_and_strict_mode(self):
        for path in PROMOTED:
            self.assertTrue(any(LICENSE_LINE in line for line in read_text(path).splitlines()[:3]), path)
        for path in (START_HOST, RUN_CYCLE):
            self.assertRegex(read_text(path), r"(?m)^set -euo pipefail$")
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, f"{path} is not executable")

    def test_identities_come_from_the_environment_only(self):
        names = "|".join(self.IDENTITIES)
        for path, required in self.REQUIRED.items():
            text = read_text(path)
            for name in required:
                self.assertRegex(text, rf"(?m)^need {name} ", f"{os.path.basename(path)} does not fail closed on {name}")
            self.assertIsNone(re.search(rf"(?m)^\s*(export\s+)?({names})=", text), "an identity is assigned inside a committed script")
            self.assertIsNone(re.search(rf"\$\{{({names}):?[-=]", text), "an identity has a default inside a committed script")
            self.assertIn('ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"', text, "the checkout is found from the script, not from a fixed path")
            self.assertIsNone(re.search("[A-Za-z]\"\"[A-Za-z]|[A-Za-z]''[A-Za-z]", text), "adjacent-quote fragment idiom")
        # Every value handed to the host or to the runner is a variable expansion, never a literal.
        self.assertRegex(read_text(START_HOST), r"--robot\.id='\$KC_ROBOT_ID' --robot\.port='\$KC_SERVO_PORT' ")
        self.assertRegex(read_text(RUN_CYCLE), r'--remote-ip "\$KC_PI_IP" --robot-id "\$KC_ROBOT_ID" ')

    def test_start_host_keeps_the_preflights_and_the_two_safety_flags(self):
        text = read_text(START_HOST)
        for fragment in ("pgrep -af 'lekiwi_host|lerobot'", '[ -e "$PORT" ]', 'fuser "$(readlink -f "$PORT")"', "for n in /dev/video*", "sha256sum",
                         "--robot.disable_torque_on_disconnect=false", "'--robot.cameras={}'", "--robot.max_relative_target=15.0", 'SCAN_ENV="KC_SCAN_MODE=1"'):
            self.assertIn(fragment, text)
        self.assertLess(text.index("pgrep -af"), text.index("scp -q"), "nothing is copied before the preflight passed")
        self.assertLess(text.index("sha256sum"), text.index("ssh -t"), "the host is not started before the copy was verified")

    def test_run_cycle_keeps_the_host_and_scan_mode_preflight_before_the_runner(self):
        text = read_text(RUN_CYCLE)
        for fragment in ("pgrep -f '[l]ekiwi_host_safe_attach.py'", "grep -qx 'KC_SCAN_MODE=1'", "/proc/$pid/environ", '[ "$pre" -eq 0 ] ||'):
            self.assertIn(fragment, text)
        self.assertLess(text.index('[ "$pre" -eq 0 ] ||'), text.index("run_single_cube_cycle.py\" --do"), "the runner starts only after the preflight verdict")


@unittest.skipUnless(shutil.which("bash"), "bash is not installed")
class LauncherRefusals(unittest.TestCase):
    """The launchers are RUN, but only into their refusals, and with stub ssh/scp/python3 first on PATH: a stub leaves a
    marker and fails, so a launcher that got past its refusal would fail this test instead of reaching anything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stubs, self.marker = os.path.join(self.tmp.name, "stubs"), os.path.join(self.tmp.name, "reached")
        os.mkdir(self.stubs)
        for tool in ("ssh", "scp", "python3"):
            path = os.path.join(self.stubs, tool)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"#!/bin/sh\necho {tool} >>'{self.marker}'\nexit ${{STUB_RC:-99}}\n")
            os.chmod(path, 0o755)
        # Reserved example values only (192.0.2.0/24, .invalid); the ssh target is assembled so that no committed line has the shape.
        self.identities = {"KC_PI_SSH": "operator" + "@" + "robot.invalid", "KC_PI_IP": "192.0.2.10", "KC_ROBOT_ID": "example_robot", "KC_SERVO_PORT": "/dev/ttyEXAMPLE",
                           "KC_WRIST_CAMERA_ID": "example-wrist", "KC_FRONT_CAMERA_ID": "example-front", "KC_CYCLE_LOG_DIR": os.path.join(self.tmp.name, "cycle-logs")}

    def run_script(self, script, *args, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
        base["PATH"] = self.stubs + os.pathsep + base.get("PATH", "")
        return subprocess.run(["bash", script, *args], env={**base, **env}, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def reached(self):
        return read_text(self.marker).split() if os.path.exists(self.marker) else []

    def test_both_launchers_fail_closed_without_identities(self):
        for script, args in ((START_HOST, ()), (RUN_CYCLE, ("observe",))):
            result = self.run_script(script, *args)
            self.assertEqual(result.returncode, 5, result.stderr)
            for name in LauncherShape.REQUIRED[script]:
                self.assertIn(f"REFUSE: {name} is not set", result.stderr)
        self.assertEqual(self.reached(), [], "a launcher without identities must not reach ssh, scp or the runner")

    def test_one_missing_identity_is_enough(self):
        for script, args in ((START_HOST, ()), (RUN_CYCLE, ("observe",))):
            for name in LauncherShape.REQUIRED[script]:
                result = self.run_script(script, *args, **{k: v for k, v in self.identities.items() if k != name})
                self.assertEqual(result.returncode, 5, f"{os.path.basename(script)} ran without {name}")
        self.assertEqual(self.reached(), [])

    def test_start_host_refuses_a_value_it_cannot_quote_and_never_echoes_it(self):
        hostile = "x'; touch injected; '"
        result = self.run_script(START_HOST, **{**self.identities, "KC_ROBOT_ID": hostile})
        self.assertEqual(result.returncode, 5)
        self.assertNotIn(hostile, result.stdout + result.stderr)
        self.assertEqual(self.run_script(START_HOST, "bogus", **self.identities).returncode, 5, "an unknown mode is refused, not read as normal mode")
        self.assertEqual(self.reached(), [])

    def test_run_cycle_never_commands_when_the_preflight_did_not_answer(self):
        # 255 is what ssh returns for a dropped connection: it must not pass for "the host is not in scan mode".
        for status in ("255", "3", "1"):
            result = self.run_script(RUN_CYCLE, "observe", STUB_RC=status, **self.identities)
            self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(set(self.reached()), {"ssh"}, "the runner must not start after a failed preflight")

    def test_run_cycle_refuses_a_log_directory_that_git_would_track(self):
        result = self.run_script(RUN_CYCLE, "observe", **{**self.identities, "KC_CYCLE_LOG_DIR": os.path.join(ROOT, "docs", "cycle-logs")})
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "docs", "cycle-logs")))
        self.assertEqual(self.reached(), [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "bash or sha256sum is not installed")
class StartHostHarness(unittest.TestCase):
    """start-host.sh is RUN to its end against stubs that ANSWER and reach nothing. ssh and scp log their arguments and
    the script each ssh call was handed on stdin. The ssh stub answers the checksum of whichever copied file was named,
    answers the ONE call that asks where lerobot is installed on the robot computer, and serves the text of whichever
    installed file a 'cat' call asked for, out of a tree that lives on this computer; python3 stands in for the verifier
    and records what it was asked for. Nothing reaches a network, a robot computer or a real lerobot, and the source
    check is exercised where it now runs, which is here. There are no tests in this class: it is the harness the two
    below share."""

    # The path the stub gives for the installed lerobot is a made-up one that names no account, and what a 'cat' call
    # gets back is the file of the same basename from the tree below: a test can take a file away or make a read fail
    # without any path of a real robot computer being involved.
    SSH_STUB = """#!/bin/bash
printf '%s\\t%s\\n' ssh "$*" >>'{log}'
{{ printf '=== %s\\n' "$*"; cat; }} >>'{scripts}'
case "$*" in
  *PYBIN=*)
    [ -z "$STUB_LOCATE_RC" ] || exit "$STUB_LOCATE_RC"
    printf '%s\\n' "$STUB_LEKIWI_DIR"
    exit 0 ;;
  *"cat '"*)
    [ -z "$STUB_FETCH_RC" ] || exit "$STUB_FETCH_RC"
    line="$*"
    name=${{line##*/}}
    name=${{name%\\'}}
    [ -f "$STUB_SOURCE_TREE/$name" ] || exit 1
    cat "$STUB_SOURCE_TREE/$name"
    exit 0 ;;
  *sha256sum*host_boundary.py*) printf '%s  copied\\n' "$STUB_SUM_BOUNDARY" ;;
  *sha256sum*) printf '%s  copied\\n' "$STUB_SUM" ;;
esac
exit 0
"""
    SCP_STUB = """#!/bin/sh
printf '%s\\t%s\\n' scp "$*" >>'{log}'
exit 0
"""
    # The verifier, stood in for: it records the whole argument vector it was handed and answers with the status the
    # test chose for that mode, so a test can say what drifted, what reached no verdict, and what was never read.
    PYTHON_STUB = """#!/bin/sh
printf '%s\\n' "$*" >>"$PASS_LOG"
case "$2" in
  --verify-robot) exit "${ROBOT_RC:-0}" ;;
  --verify-stock-host) exit "${STOCK_RC:-0}" ;;
esac
exit 0
"""
    INSTALLED_FILES = ("lekiwi.py", "config_lekiwi.py", "lekiwi_host.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stubs, self.log = os.path.join(self.tmp.name, "stubs"), os.path.join(self.tmp.name, "calls")
        self.scripts = os.path.join(self.tmp.name, "remote-scripts")
        self.passes = os.path.join(self.tmp.name, "passes")
        self.scratch = os.path.join(self.tmp.name, "scratch")  # the launcher's own TMPDIR, so a test can say what it left there
        self.installed_tree = os.path.join(self.tmp.name, "installed")
        for directory in (self.stubs, self.scratch, self.installed_tree):
            os.mkdir(directory)
        for name in self.INSTALLED_FILES:
            with open(os.path.join(self.installed_tree, name), "w", encoding="utf-8") as handle:
                handle.write("stands in for the installed %s\n" % name)
        for tool, template in (("ssh", self.SSH_STUB), ("scp", self.SCP_STUB)):
            path = os.path.join(self.stubs, tool)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(template.format(log=self.log, scripts=self.scripts))
            os.chmod(path, 0o755)
        with open(os.path.join(self.stubs, "python3"), "w", encoding="utf-8") as handle:
            handle.write(self.PYTHON_STUB)
        os.chmod(os.path.join(self.stubs, "python3"), 0o755)
        self.identities = {"KC_PI_SSH": "operator" + "@" + "robot.invalid", "KC_ROBOT_ID": "example_robot", "KC_SERVO_PORT": "/dev/ttyEXAMPLE"}
        # Assembled from parts and rooted where no account lives: this file is scanned for identity shapes too.
        self.installed_dir = "/".join(["", "opt", "example", "lerobot", "robots", "lekiwi"])
        with open(WRAPPER, "rb") as handle:
            self.wrapper_sum = hashlib.sha256(handle.read()).hexdigest()
        with open(BOUNDARY, "rb") as handle:
            self.boundary_sum = hashlib.sha256(handle.read()).hexdigest()

    def forget(self):
        """Start the next run with an empty record, so that one test can make several."""
        for path in (self.log, self.scripts, self.passes):
            if os.path.exists(path):
                os.remove(path)

    def start(self, *args, checksum=None, boundary_checksum=None, **stub_env):
        base = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
        base["PATH"] = self.stubs + os.pathsep + base.get("PATH", "")
        base["TMPDIR"] = self.scratch
        stubs = {"STUB_SUM": self.wrapper_sum if checksum is None else checksum,
                 "STUB_SUM_BOUNDARY": self.boundary_sum if boundary_checksum is None else boundary_checksum,
                 "STUB_LEKIWI_DIR": self.installed_dir, "STUB_SOURCE_TREE": self.installed_tree,
                 "PASS_LOG": self.passes, "STUB_LOCATE_RC": "", "STUB_FETCH_RC": ""}
        result = subprocess.run(["bash", START_HOST, *args], env={**base, **self.identities, **stubs, **stub_env},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, cwd=self.tmp.name)
        return result, [line.split("\t", 1) for line in read_text(self.log).splitlines()]

    def remote_scripts(self):
        """(the ssh command line, the script that call was handed on stdin) for every ssh call, in the order they were
        made. A call that was handed nothing, the host start among them, has the empty string."""
        if not os.path.exists(self.scripts):
            return []
        records = [record for record in re.split(r"(?m)^=== ", read_text(self.scripts)) if record]
        return [(record.split("\n", 1)[0], record.split("\n", 1)[1]) for record in records]

    def verifier_calls(self):
        """The argument vector of every call the launcher made to the verifier, in order."""
        return [line.split() for line in read_text(self.passes).splitlines()] if os.path.exists(self.passes) else []

    def made(self):
        """The modes the verifier was asked for, in order."""
        return [call[1] for call in self.verifier_calls()]

    @staticmethod
    def reads(sent):
        """The basenames the launcher asked the robot computer to read out to it, in the order it asked."""
        return [line.rsplit("/", 1)[1].rstrip("'") for _, line in sent if "cat '" in line]


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "bash or sha256sum is not installed")
class StartHostCommandLines(StartHostHarness):
    """What would have been SENT to the robot computer, which the text fragments checked in LauncherShape cannot prove:
    that the checksum verdict gates the start, that what TRAVELS is decided by the mode and by nothing else, that the
    source check only READS the robot computer, and that the two safety flags and the identities really arrive in the
    remote command line."""

    def test_a_copy_that_does_not_match_is_never_started(self):
        result, sent = self.start(checksum="0" * 64)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual([tool for tool, _ in sent], ["ssh", "ssh", "scp", "ssh"], "preflight, mkdir, copy, checksum: and nothing after the mismatch")
        self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line], "the host was started from a copy that was not verified")
        self.assertEqual(self.made(), [], "the installed source was read although the copy that would have run it was already known not to match")

    def test_a_normal_start_copies_the_wrapper_alone_and_the_boundary_module_never_leaves_this_laptop(self):
        result, sent = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        copied = [line for tool, line in sent if tool == "scp"]
        self.assertEqual(len(copied), 1, "a start without the word copies the ONE file it copied before the source check existed")
        self.assertTrue(copied[0].endswith("/lekiwi_host_safe_attach.py"), copied)
        self.assertFalse([line for _, line in sent if "host_boundary.py" in line], "host_boundary.py was named in a call to the robot computer on a plain start")
        self.assertEqual(self.made(), ["--verify-robot"], "the source check still runs on a plain start; it just runs here")
        self.assertEqual(self.verifier_calls()[0][0], BOUNDARY, "the verifier that ran is not this checkout's own bin/pi/host_boundary.py")
        self.assertNotIn("KC_HOST_BOUNDARY", sent[-1][1], "a plain start must not name that variable in the host's environment")

    def test_a_start_with_the_word_copies_the_boundary_module_and_checksums_it_there(self):
        result, sent = self.start("boundary")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        copied = [line for tool, line in sent if tool == "scp"]
        self.assertEqual(len(copied), 2, "with the word the module travels, because the wrapper imports it there")
        self.assertTrue(any(line.endswith("/host_boundary.py") for line in copied), copied)
        self.assertTrue(any("sha256sum" in line and "host_boundary.py" in line for _, line in sent), "the copy of the boundary module is verified like the wrapper's")
        self.assertIn("KC_HOST_BOUNDARY=1", sent[-1][1], "and the host that is started is the one that will install it")

    def test_a_boundary_module_whose_copy_does_not_match_is_never_started_either(self):
        result, sent = self.start("boundary", boundary_checksum="0" * 64)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual([tool for tool, _ in sent], ["ssh", "ssh", "scp", "ssh", "scp", "ssh"], "the second copy is where it stopped")
        self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line], "the host would have imported an unverified host_boundary.py")

    def test_a_start_without_the_word_fetches_the_robot_source_and_asks_for_no_stock_host_pass(self):
        result, sent = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line for _, line in sent]
        locate = [line for line in lines if "PYBIN=" in line]
        self.assertEqual(len(locate), 1, "exactly one call asks the robot computer where its lerobot is installed")
        self.assertEqual(self.reads(sent), ["lekiwi.py", "config_lekiwi.py"],
                         "a plain start read the loop the boundary would have patched, or did not read the configuration --verify-robot needs beside lekiwi.py")
        self.assertEqual(self.made(), ["--verify-robot"])
        self.assertTrue(self.verifier_calls()[0][2].startswith(self.scratch), "the verifier was pointed at something other than the copy on this laptop")
        self.assertLess(max(i for i, line in enumerate(lines) if "sha256sum" in line), lines.index(locate[0]), "the source check ran before the copies were verified")
        after = lines[lines.index(locate[0]) + 1:]
        self.assertEqual([line for line in after[:-1] if "cat '" in line], after[:-1], "the source check's reads are the last thing before the host")
        self.assertIn("lekiwi_host_safe_attach.py --robot", after[-1], "and the host is what came after them")

    def test_a_start_with_the_word_fetches_the_stock_host_loop_as_well(self):
        result, sent = self.start("boundary")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.reads(sent), ["lekiwi.py", "config_lekiwi.py", "lekiwi_host.py"])
        self.assertEqual(self.made(), ["--verify-robot", "--verify-stock-host"], "one read per file, both passes made here")

    def test_the_source_check_only_reads_the_robot_computer(self):
        # The whole point of moving it here: a start that verifies the installed source must put nothing there to do it.
        result, sent = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after_copies = [line for _, line in sent][4:]  # preflight, mkdir, the one copy and its checksum come first
        self.assertTrue(len(after_copies) > 1, "the source check made no call at all")
        for line in after_copies[:-1]:  # everything up to the host itself
            self.assertTrue("PYBIN=" in line or "cat '" in line, f"the source check made a call that is neither the one question nor a read: {line}")
            for writing in (">", "mkdir", "touch", "tee", "rm ", "scp"):
                self.assertNotIn(writing, line, f"a call of the source check could write on the robot computer: {line}")
        locate = [script for line, script in self.remote_scripts() if "PYBIN=" in line][0]
        for writing in ("mkdir", "touch", "tee", "cp ", "rm ", "scp"):
            self.assertNotIn(writing, locate, "the script that asks where lerobot is installed could write on the robot computer")
        self.assertEqual(re.findall(r"(?<![0-9&])>(?!&)\s*[^\s&|;]+", locate), [],
                         "the script that asks where lerobot is installed redirects into a file on the robot computer")

    def test_the_copies_it_made_here_are_gone_when_it_is_done(self):
        for args, expected, stub_env in (((), 0, {}), ((), 3, {"ROBOT_RC": "1"}), (("boundary",), 3, {"STOCK_RC": "1"})):
            self.forget()
            result, _ = self.start(*args, **stub_env)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertEqual([name for name in os.listdir(self.scratch) if name.startswith("kc-installed-source")], [],
                             "the copies of the installed lerobot source were left behind on this laptop")

    def test_the_host_command_line_carries_the_safety_flags_and_the_identities_from_the_environment(self):
        for args, scan in (((), False), (("scan",), True)):
            self.forget()
            result, sent = self.start(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([tool for tool, _ in sent], ["ssh", "ssh", "scp", "ssh", "ssh", "ssh", "ssh", "ssh"],
                             "preflight, mkdir, ONE copy with its checksum, the one question, two reads, the host")
            self.assertTrue(all(self.identities["KC_PI_SSH"] in line for _, line in sent), "every call goes to the target given in the environment")
            self.assertIn("PORT='/dev/ttyEXAMPLE' bash -s", sent[0][1], "the preflight checks the bus given in the environment")
            host = sent[-1][1]
            self.assertTrue(host.startswith("-t "), "the host needs a real terminal so that Ctrl-C reaches it as SIGINT")
            for fragment in ("--robot.id='example_robot'", "--robot.port='/dev/ttyEXAMPLE'", "--robot.disable_torque_on_disconnect=false", "'--robot.cameras={}'", "--robot.max_relative_target=15.0"):
                self.assertIn(fragment, host)
            self.assertEqual("KC_SCAN_MODE=1 " in host, scan, "scan mode is sent when asked for, and only then")


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "bash or sha256sum is not installed")
class SourceCheckOnTheLaptop(StartHostHarness):
    """The source check itself, now that it runs HERE: which passes are made in which mode, and that what the operator
    is told keeps a source that drifted apart from a check that never reached a verdict. The launcher is run whole, so
    these are the consequences of the mode, not the flag that was meant to produce them."""

    def test_the_question_it_asks_the_robot_computer_is_a_quoted_here_document(self):
        # An UNQUOTED delimiter would let this laptop expand its own values into the script the robot computer runs,
        # which is how an identity reaches a remote shell without anyone deciding to put it there.
        body = re.search(r"(?s)<<'LOCATE'\n(.*?)\nLOCATE\n", read_text(START_HOST))
        self.assertIsNotNone(body, "the question start-host.sh asks the robot computer is no longer a here-document with a quoted delimiter")
        self.assertNotIn("$KC", body.group(1), "the quoted here-document must not expand a laptop-side value into the script the robot computer runs")

    def test_a_start_without_the_boundary_runs_the_robot_pass_alone(self):
        result, _ = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.made(), ["--verify-robot"], "a start that installs no boundary read the loop the boundary would have patched")

    def test_a_start_with_the_boundary_runs_both_passes_and_the_robot_one_first(self):
        result, _ = self.start("boundary")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.made(), ["--verify-robot", "--verify-stock-host"])

    def test_a_stock_host_loop_that_drifted_does_not_refuse_a_start_that_will_not_patch_it(self):
        # The over-refusal this split exists for: with no boundary the host loop's shape decides nothing, and a start
        # refused over it is a start refused for a reason that does not apply to it.
        result, _ = self.start(STOCK_RC="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.made(), ["--verify-robot"])

    def test_a_robot_source_that_drifted_refuses_either_way_and_names_only_what_drifted(self):
        for args in ((), ("boundary",)):
            self.forget()
            result, sent = self.start(*args, ROBOT_RC="1")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("robot source installed", result.stderr)
            self.assertNotIn("stock host loop installed", result.stderr)
            self.assertIn("NOTHING was started", result.stderr)
            self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line], "the host was started although the robot source drifted")

    def test_each_pass_that_drifted_is_named_on_its_own(self):
        for stub_env, robot, stock in (({"STOCK_RC": "1"}, False, True), ({"ROBOT_RC": "1", "STOCK_RC": "1"}, True, True)):
            self.forget()
            result, _ = self.start("boundary", **stub_env)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual("robot source installed" in result.stderr, robot, f"{stub_env} named the robot source wrongly")
            self.assertEqual("stock host loop installed" in result.stderr, stock, f"{stub_env} named the stock host loop wrongly")

    def test_a_pass_that_found_something_does_not_keep_the_next_one_from_running(self):
        result, _ = self.start("boundary", ROBOT_RC="1")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.made(), ["--verify-robot", "--verify-stock-host"], "the operator must be told everything that drifted at once")

    def test_a_pass_that_reached_no_verdict_is_never_counted_as_drift(self):
        for status in ("2", "127"):
            self.forget()
            result, sent = self.start(ROBOT_RC=status)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("could not be run", result.stdout + result.stderr)
            self.assertIn("could NOT BE RUN", result.stderr)
            self.assertNotIn("no longer holds", result.stderr, f"status {status} was reported to the operator as a source that changed")
            self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line], f"the host was started although a pass exited {status}")

    def test_a_file_that_did_not_come_back_is_a_pass_that_could_not_be_run(self):
        os.remove(os.path.join(self.installed_tree, "lekiwi_host.py"))
        result, sent = self.start("boundary")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.made(), ["--verify-robot"], "the pass whose file did arrive must still be made")
        self.assertIn("did not come back", result.stderr)
        self.assertIn("could NOT BE RUN", result.stderr)
        self.assertNotIn("no longer holds", result.stderr)
        self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line])

    def test_a_configuration_that_is_not_there_is_left_for_the_verifier_to_report(self):
        # config_lekiwi.py is read BY --verify-robot, beside lekiwi.py, and its absence is a verdict that verifier
        # reaches in its own words. Turning it into "the check could not be run" here would take that verdict away.
        os.remove(os.path.join(self.installed_tree, "config_lekiwi.py"))
        result, _ = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.made(), ["--verify-robot"])
        self.assertNotIn("could NOT BE RUN", result.stderr)

    def test_no_installed_lerobot_refuses_before_any_file_is_read(self):
        result, sent = self.start(STUB_LOCATE_RC="3")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.made(), [])
        self.assertEqual(self.reads(sent), [], "a file was read although the robot computer never said where lerobot is")
        self.assertIn("could NOT BE RUN", result.stderr)
        self.assertNotIn("no longer holds", result.stderr)

    def test_ssh_that_stopped_reaching_the_robot_computer_is_never_read_as_drift(self):
        for stub_env in ({"STUB_LOCATE_RC": "255"}, {"STUB_FETCH_RC": "255"}):
            self.forget()
            result, sent = self.start(**stub_env)
            self.assertEqual(result.returncode, 255, result.stdout + result.stderr)
            self.assertIn("NOTHING was started", result.stderr)
            self.assertNotIn("no longer holds", result.stderr)
            self.assertEqual(self.made(), [])
            self.assertFalse([line for _, line in sent if "lekiwi_host_safe_attach.py --robot" in line])

    def test_a_directory_it_could_not_quote_is_refused_rather_than_sent(self):
        # The path comes back from the robot computer and goes into a command line that computer's own shell parses, so
        # it is held to the character set the identities are held to, and the value itself is never printed.
        hostile = "/opt/example'; touch injected; '"
        result, sent = self.start(STUB_LEKIWI_DIR=hostile)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn(hostile, result.stdout + result.stderr)
        self.assertEqual(self.reads(sent), [])
        self.assertEqual(self.made(), [])
        self.assertIn("could NOT BE RUN", result.stderr)

    def test_nothing_it_prints_carries_the_path_of_the_installed_lerobot(self):
        # Those paths name the account the robot computer runs as, and this output ends up in a terminal log.
        for args, stub_env in (((), {}), ((), {"ROBOT_RC": "1"}), ((), {"ROBOT_RC": "127"}), (("boundary",), {"STOCK_RC": "1"})):
            self.forget()
            result, _ = self.start(*args, **stub_env)
            self.assertNotIn(self.installed_dir, result.stdout + result.stderr, f"{stub_env} printed where the installed lerobot lives")
        self.forget()
        os.remove(os.path.join(self.installed_tree, "lekiwi.py"))
        result, _ = self.start()
        self.assertEqual(result.returncode, 3)
        self.assertNotIn(self.installed_dir, result.stdout + result.stderr)

    def test_a_pass_that_holds_is_never_described_as_a_promise_that_the_attach_will_pass(self):
        # A source that holds and an attach that refuses are both ordinary and can be true on the same start: the attach
        # reads the LIVE registers, and a power cycle leaves torque off and every goal far from its present position.
        result, _ = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("It does not say this attach will pass", result.stdout, "the operator is not told what a passing source check leaves open")
        self.assertIn("Acquire the arm first", result.stdout)
        header = " ".join(line.lstrip("# ").strip() for line in read_text(START_HOST).splitlines() if line.startswith("#"))
        self.assertIn("IT IS NOT A STATEMENT THAT THE HOST WILL ATTACH", header)
        for fact in ("ATTACH_TOLERANCE_TICKS", "torque is not ON", "power cycle"):
            self.assertIn(fact, header, f"the header does not say why a source check that passes can sit next to an attach that refuses: {fact}")


@unittest.skipUnless(shutil.which("bash"), "bash is not installed")
class RunCyclePreflightBody(unittest.TestCase):
    """The script that run-cycle.sh sends to the robot computer, taken out of the launcher and run HERE with a stub
    pgrep first on PATH, against child processes of this test. It reaches no network and looks at no real host."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        body = re.search(r"<<'PRE' \|\| pre=\$\?\n(.*?)\nPRE\n", read_text(RUN_CYCLE), re.S)
        self.assertIsNotNone(body, "the remote preflight of run-cycle.sh is no longer a quoted here-document")
        self.body = body.group(1)
        self.assertNotIn("KC_PI", self.body, "the quoted here-document must not depend on a laptop-side value")

    def child(self, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
        process = subprocess.Popen(["sleep", "30"], env={**clean, **env}, stdin=subprocess.DEVNULL)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def preflight(self, *pids):
        stub = os.path.join(self.tmp.name, "pgrep")
        with open(stub, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n" + (f"printf '%s\\n' {' '.join(str(p) for p in pids)}\nexit 0\n" if pids else "exit 1\n"))
        os.chmod(stub, 0o755)
        return subprocess.run(["bash", "-c", self.body], env={**os.environ, "PATH": self.tmp.name + os.pathsep + os.environ.get("PATH", "")}, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def test_no_host_refuses(self):
        result = self.preflight()
        self.assertEqual(result.returncode, 3)
        self.assertIn("not running", result.stdout)

    def test_a_normal_mode_host_passes_and_a_scan_mode_host_refuses(self):
        normal = self.child()
        self.assertEqual(self.preflight(normal).returncode, 0)
        result = self.preflight(normal, self.child(KC_SCAN_MODE="1"))
        self.assertEqual(result.returncode, 3, "ONE matched process in scan mode is enough")
        self.assertIn("SCAN MODE", result.stdout)
        self.assertEqual(self.preflight(self.child(KC_SCAN_MODE="0"), self.child(KC_SCAN_MODE="11")).returncode, 0, "only the exact opt-in value is scan mode, as in the wrapper")

    @unittest.skipIf(os.geteuid() == 0, "root can read every environment")
    def test_an_environment_that_cannot_be_read_refuses_even_next_to_one_that_can(self):
        for pids in ((1,), (self.child(), 1)):  # pid 1 belongs to another user: its environment is denied to this one
            result = self.preflight(*pids)
            self.assertEqual(result.returncode, 3, "an unknown mode must never pass for normal mode")
            self.assertIn("cannot be read", result.stdout)

    def test_a_process_that_is_gone_is_not_a_running_host(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.assertEqual(self.preflight(gone.pid).returncode, 3)


class NoIdentityInPromotedFiles(unittest.TestCase):
    """Generic SHAPES of an identity, built at runtime. This cannot know the real values (they live outside git on
    purpose); the repository gate with the private token list is what checks those."""

    @staticmethod
    def findings(text):
        found = []
        for match in re.finditer(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])", text):
            if match.groups()[:3] != ("192", "0", "2") and match.groups() != ("127", "0", "0", "1"):  # 192.0.2.0/24 is reserved for documentation; the loopback address names no machine
                found.append(("ipv4", match.group(0)))
        home = "/" + "home" + "/"
        found += [("home path", m.group(0)) for m in re.finditer(re.escape(home) + r"\S*", text)]
        for bus in ("serial", "v4l"):
            for kind in ("by-id", "by-path"):
                prefix = "/".join(["", "dev", bus, kind, ""])
                found += [("device identity", m.group(0)) for m in re.finditer(re.escape(prefix) + r"[A-Za-z0-9][^\s'\"`]*", text)]
        found += [("account at host", m.group(0)) for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*", text)]
        reserved_mac = ":".join(["00", "00", "5e", "00", "53", "00"])
        found += [("mac", m.group(0)) for m in re.finditer(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", text) if m.group(0).lower() != reserved_mac]
        return found

    def test_the_patterns_catch_what_they_are_for(self):
        samples = {
            "ipv4": ".".join(["192", "168", "7", "20"]),
            "home path": "/" + "home" + "/" + "someone/checkout",
            "device identity": "/".join(["", "dev", "serial", "by-id", "usb-Example_Adapter_0123-if00"]),
            "account at host": "someone" + "@" + "robot",
            "mac": ":".join(["aa", "bb", "cc", "dd", "ee", "ff"]),
        }
        for kind, sample in samples.items():
            self.assertIn(kind, [k for k, _ in self.findings(f"x {sample} y")], kind)
        allowed = " ".join(["192.0.2.10", "127.0.0.1", "/".join(["", "dev", "serial", "by-id", ""]) + "<entry>", "/".join(["", "dev", "serial", "by-id", ""]) + " entry",
                            '"${SSH[@]}" "$@"', "<account>@<address>", ":".join(["00", "00", "5e", "00", "53", "00"]), "A-Za-z0-9_./:@+=-", "version 0.6.1"])
        self.assertEqual(self.findings(allowed), [])

    def test_promoted_files_carry_no_identity_shaped_string(self):
        for path in PROMOTED:
            self.assertEqual(self.findings(read_text(path)), [], f"identity-shaped string in {os.path.relpath(path, ROOT)}")

    def test_promoted_files_pass_the_committed_text_rules(self):
        em_dash, symbols = chr(0x2014), re.compile("[" + chr(0x2600) + "-" + chr(0x27BF) + chr(0x1F300) + "-" + chr(0x1FAFF) + "]")
        layout = re.compile("|".join(["vendor/[a-z]", "crates/[a-z]", "fixtures" + "/gate"]))
        for path in PROMOTED:
            text = read_text(path)
            self.assertNotIn(em_dash, text, path)
            self.assertIsNone(symbols.search(text), path)
            if path != os.path.abspath(__file__):  # this file has to spell the patterns it looks for
                self.assertIsNone(layout.search(text), path)


if __name__ == "__main__":
    unittest.main()
